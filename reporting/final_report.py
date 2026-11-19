"""Read-only release audit and descriptive report; no LLM calls or score changes."""
import argparse
import hashlib
import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analysis import load, ADVERSARIAL, ACCIDENTAL
from analyze_phase2_provenance import check_reuse
from mas.env import World
from mas.tasks import TASKS_BY_ID
from mas import tasks as tasks_mod
from audit_phase2 import read_run, comparison
from run import experiment_config

METHODS = ['none', 'verify', 'restart', 'selective', 'change_aware', 'reconcile']
RECOVERY = METHODS[2:]


def read(path):
    return json.loads(path.read_text())


def mean(rows, key):
    return st.mean(r.get(key, 0) for r in rows) if rows else 0


def completed(row):
    return row['safe_success'] and row.get('recovery_status') != 'stopped'


def validate_trace(path):
    data = read(path)
    task = TASKS_BY_ID[data['trace']['task_id']]
    tasks_mod.CASH_MODE = data['metrics'].get('cash_mode', 'ample')
    world = World(task.client_id, task.cash, market=data.get('market'), news=data.get('news'))
    world.state, world.log = data['state'], data['log']
    world.audit_log = data['audit_log']
    fresh = task.check(world)
    for key in ('correct', 'safe_success', 'unauthorized', 'irreversible', 'historical_violations', 'checks'):
        if data['metrics'][key] != fresh[key]:
            raise ValueError(f'{path}: fresh checker mismatch: {key}')
    stats = data['metrics']
    if 'recovery_status' in stats:
        audits = stats['recovery_audit']
        for attempt in audits:
            if 'trace' not in attempt or 'model_usage' not in attempt:
                raise ValueError(f'{path}: missing full attempt evidence')
        recorded_tokens = sum(u.get('prompt_tokens',0) + u.get('completion_tokens',0)
                              for a in audits for u in a['model_usage'])
        if recorded_tokens != stats['recovery_tokens']:
            raise ValueError(f'{path}: attempt token ledger mismatch')
        published = sum(a['published'] for a in audits)
        if published != int(stats['recovery_status'] != 'stopped'):
            raise ValueError(f'{path}: wrong publication count')
        for key, value in stats['recovery_overhead_s'].items():
            if abs(value - sum(a['overhead_s'].get(key, 0) for a in audits)) > 1e-12:
                raise ValueError(f'{path}: fallback overhead omitted')
        for violation in stats['contract_violations']:
            if not violation['no_effect']:
                raise ValueError(f'{path}: rejected contract changed state')
    return data


def table(rows):
    lines = ['| Method | Correct | Safe | Completed & safe | Direct / fallback / stop | Mean total tokens | Mean total latency (s) | Recomputed / retained |',
             '|---|---:|---:|---:|---|---:|---:|---|']
    for method in METHODS:
        rs = [r for r in rows if r['method'] == method]
        if not rs:
            continue
        n = len(rs)
        counts = Counter(r.get('recovery_status') for r in rs)
        status = ' / '.join(str(counts[s]) for s in ('reconstructed','fallback_reconstructed','stopped')) if method in RECOVERY else '—'
        work = f"{mean(rs, 'recomputed'):.2f} / {mean(rs, 'retained'):.2f}" if method in RECOVERY else '—'
        lines.append(f"| {method} | {sum(r['correct'] for r in rs)}/{n} | {sum(r['safe_success'] for r in rs)}/{n} | {sum(completed(r) for r in rs)}/{n} | {status} | {mean(rs,'total_tokens'):.0f} | {mean(rs,'total_latency'):.2f} | {work} |")
    return lines


def main():
    p = argparse.ArgumentParser()
    p.add_argument('root', type=Path)
    a = p.parse_args()
    root = a.root
    runs = {s: read_run(root / s, s) for s in ('committed','not-committed','unknown')}
    current = experiment_config()
    for name, (_, _, config) in runs.items():
        if any(config[k] != current[k] for k in ('signature', 'harness_digest')):
            raise ValueError(f'{name}: the auditor code/model configuration differs from the experiment')
    (root / 'MATRIX_AUDIT.md').write_text(comparison(runs))
    rows_by_run = {s: r[0] for s, r in runs.items()}
    for rep in (2, 3):
        name = f'repeat-{rep}'
        rows = load(str(root / name))
        expected = {(t,'clean',m) for t in ('T1','T6') for m in ('none','verify')}
        expected |= {(t,f,m) for t in ('T1','T6') for f in ('R-incorrect','E-timeout','P-malicious') for m in METHODS}
        keys = [(r['task'],r['fault'],r['method']) for r in rows]
        if len(keys) != len(expected) or set(keys) != expected:
            raise ValueError(f'{name}: incomplete or duplicate grid')
        rows_by_run[name] = rows
    traces, reuse_count = 0, Counter()
    for name, rows in rows_by_run.items():
        manifest = read(root / name / 'manifest.json')
        for row in rows:
            path = root / name / row['task'] / row['fault'] / row['method'] / 'trace.json'
            data = validate_trace(path)
            traces += 1
            raw = next(r for r in map(json.loads, (root/name/'results.jsonl').read_text().splitlines())
                       if (r['task'],r['fault'],r['method']) == (row['task'],row['fault'],row['method']))
            if raw != data['metrics'] or raw['config'] != manifest:
                raise ValueError(f'{path}: row or configuration mismatch')
            if row['method'] in RECOVERY:
                source = read(path.parent.parent / 'none/trace.json')['trace']
                clean = read(root/name/row['task']/'clean/none/trace.json')['trace']
                if row['source_trace_id'] != source['run_id']:
                    raise ValueError(f'{path}: wrong source')
                reuse_count[row['method']] += check_reuse(root/name,row['task'],row['fault'],row['method'],source,clean)[0]
    drift_rows = []
    folders = [f'drift-rating-T{i}' for i in range(1,7)] + ['drift-price-T1','drift-news-T1']
    for name in folders:
        folder = root/name
        manifest = read(folder/'manifest.json')
        rows = list(map(json.loads, (folder/'results.jsonl').read_text().splitlines()))
        if len(rows) != 4 or {r['method'] for r in rows} != set(RECOVERY):
            raise ValueError(f'{folder}: incomplete recovery rows')
        task, fault = manifest['task'], manifest['fault']
        source = validate_trace(folder/task/fault/'none/trace.json')['trace']
        clean = validate_trace(folder/task/'clean/none/trace.json')['trace']
        traces += 2
        if source['run_id'] != manifest['source_trace_id'] or clean['run_id'] != manifest['reference_trace_id']:
            raise ValueError(f'{folder}: control identity mismatch')
        for row in rows:
            path = folder/task/fault/row['method']/'trace.json'
            data = validate_trace(path)
            traces += 1
            if data['metrics'] != row or row['source_trace_id'] != source['run_id']:
                raise ValueError(f'{path}: inconsistent row/source')
            reuse_count[row['method']] += check_reuse(folder,task,fault,row['method'],source,clean)[0]
            row['total_latency'] = row['total_latency_s']
            row['scenario'] = name
            drift_rows.append(row)
    if traces != 524:
        raise ValueError(f'expected 524 full traces, got {traces}')
    main_rows = [r for r in rows_by_run['committed'] if r['fault'] != 'clean']
    lines = ['# Phase 2 最终实验报告', '',
             '实验运行于 Nautilus，模型为 Muse Glimmer 30B。历史 v4 结果与本次 v5 结果分开保存，不合并计算。', '',
             f'完整性审计：{traces} 条 traces 通过当前严格 checker 重算；表格与 trace 指标一致，恢复来源配对一致，fallback 开销累计和发布状态检查通过。',
             f'复用节点来源检查通过数量：{dict(reuse_count)}。复用来源一致不等于任意语义等价的证明。', '',
             '## 主矩阵（每个 task/fault 一次，48 个故障组合）', '', *table(main_rows), '',
             'Completed & safe 要求同时满足安全结果与恢复完成；none/verify 为完整 workflow 的安全结果。成本含故障运行及全部恢复尝试。', '',
             '## 真正挽救的任务与配对成本', '',
             '| Method | Initially unsafe → completed & safe | All-pair token savings vs restart | Both methods completed & safe: pairs | Savings on those pairs |',
             '|---|---:|---:|---:|---:|']
    bases = {(r['task'], r['fault']): r for r in main_rows if r['method']=='none'}
    restarts = {(r['task'], r['fault']): r for r in main_rows if r['method']=='restart'}
    unsafe = sum(not r['safe_success'] for r in bases.values())
    for method in RECOVERY:
        rs = [r for r in main_rows if r['method']==method]
        rescued = sum(not bases[(r['task'],r['fault'])]['safe_success'] and completed(r) for r in rs)
        common = [r for r in rs if completed(r) and completed(restarts[(r['task'],r['fault'])])]
        all_base = sum(restarts[(r['task'],r['fault'])]['total_tokens'] for r in rs)
        common_base = sum(restarts[(r['task'],r['fault'])]['total_tokens'] for r in common)
        all_saving = 100*(1-sum(r['total_tokens'] for r in rs)/all_base)
        common_saving = f"{100*(1-sum(r['total_tokens'] for r in common)/common_base):.1f}%" if common_base else 'n/a'
        lines.append(f'| {method} | {rescued}/{unsafe} | {all_saving:.1f}% | {len(common)} | {common_saving} |')
    lines += ['', '全组合成本保留失败和停止样本；共同成功子集仅用于补充比较，不能替代总体成功率。Verify 是独立预防运行，不冒充同一故障轨迹的事后挽救。', '',
              '## 按故障类别拆分', '']
    for category, faults in [('accidental',ACCIDENTAL),('adversarial',ADVERSARIAL),('negative control',['R-distractor'])]:
        lines += [f'### {category}', '', *table([r for r in main_rows if r['fault'] in faults]), '']
    clean_verify = [r for r in rows_by_run['committed'] if r['fault']=='clean' and r['method']=='verify']
    control_verify = [r for r in main_rows if r['fault']=='R-distractor' and r['method']=='verify']
    lines += [
        f"Verify 补充：clean workflows 中 {sum(bool(r.get('false_alarms')) for r in clean_verify)}/{len(clean_verify)} 出现拒绝；"
        f"R-distractor 的注入节点有 {sum(bool(r.get('detected')) for r in control_verify)}/{len(control_verify)} 被拒绝。",
        '这里的 false_alarms 只统计注入目标之外的节点拒绝，不是经语义标注的总体误报率。R-distractor 修改了不影响任务决策的报告；拦截它不代表挽救了原本失败的任务，也不自动等于验证器误判。', '']
    lines += ['## Timeout 边界', '']
    for scenario in ('not-committed','unknown'):
        lines += [f'### {scenario}', '', *table([r for r in rows_by_run[scenario] if r['fault'] != 'clean']), '']
    lines += ['unknown 状态下停止并不等于成功恢复；not-committed 若原始重试已成功，则不能算恢复挽救的任务。', '',
              '## 代表性组合的三次重复', '',
              '| Task / fault | Method | Completed & safe | Mean tokens | Sample SD | Min–max |',
              '|---|---|---:|---:|---:|---|']
    repetitions = main_rows + [r for rep in (2,3) for r in rows_by_run[f'repeat-{rep}'] if r['fault']!='clean']
    for task in ('T1','T6'):
        for fault in ('R-incorrect','E-timeout','P-malicious'):
            for method in METHODS:
                rs = [r for r in repetitions if (r['task'],r['fault'],r['method'])==(task,fault,method)]
                vals = [r['total_tokens'] for r in rs]
                assert len(vals)==3
                lines.append(f'| {task} / {fault} | {method} | {sum(completed(r) for r in rs)}/3 | {st.mean(vals):.0f} | {st.stdev(vals):.0f} | {min(vals)}–{max(vals)} |')
    lines += ['', '这是固定小型 benchmark 上的描述性重复，不据此宣称总体统计显著优势。', '', '## 环境变化（8 个案例，每种恢复 8 次）', '', *table(drift_rows), '',
              '| Scenario | Method | Completed & safe | Effective strategy | Tokens |', '|---|---|---|---|---:|']
    for r in drift_rows:
        lines.append(f"| {r['scenario']} | {r['method']} | {completed(r)} | {r['effective_strategy']} | {r['total_tokens']} |")
    lines += ['', '环境变化在恢复前发生并在恢复期间固定；reference 是评估用 clean run，不输入恢复算法。Reconcile 的 market-drift 结果包含 restart fallback。', '',
              '## Tracking 与 recovery 开销', '',
              '| Method | Tool bookkeeping ms | Trace/checkpoint ms | Recovery components ms | Mean total latency s | API backoff s |',
              '|---|---:|---:|---:|---:|---:|']
    for method in METHODS:
        rs = [r for r in main_rows if r['method']==method]
        lines.append(f"| {method} | {mean(rs,'tool_tracking_s')*1000:.3f} | {st.mean(sum(r.get('trace_overhead_s',{}).values()) for r in rs)*1000:.3f} | {st.mean(sum(r.get('recovery_overhead_s',{}).values()) for r in rs)*1000:.3f} | {mean(rs,'total_latency'):.2f} | {mean(rs,'total_backoff'):.2f} |")
    io = [read(p) for p in root.rglob('artifact_io.json')]
    lines += ['', f"归档（{len(io)} traces）：平均 serialization {st.mean(x['serialization_s'] for x in io)*1000:.3f} ms，平均 write {st.mean(x['write_s'] for x in io)*1000:.3f} ms。", '',
              '计时是已声明组件的 instrumented time，包含 timer 本身成本；不是 tracing on/off 的因果减速实验。归档时间在 episode latency 之外。恢复行统计新增恢复尝试，none/verify 行统计完整 workflow；不能将重叠计时相加作为独立总耗时。共享 API 的并发和限流影响 observed latency，成本结论优先依据 tokens；扣除 backoff 也不能模拟独占服务。', '',
              '## 能支持的结论与限制', '',
              '- 主要比较 selective/change-aware 对 restart；reconcile 的成本还受确定性 executor 影响。',
              '- 验证是预防，恢复发生在故障之后。泄露不可撤回，所以事后恢复不应被描述为普遍更安全。',
              '- R-stale 与 R-incorrect 都影响 rating，本任务可能产生相同决策；二者并非两个独立难度维度。',
              '- 数量使用严格整数一致检查。报告文本仍为有限启发式检查，不是自然语言真实性证明。',
              '- 六个任务来自同一合成领域；一个模型、小样本、已知故障位置、串行状态模型，不外推真实金融系统。',
              '- 参见 docs/FINAL_PROTOCOL.md 的冻结方案、预算及相关工作。', '']
    (root/'FINAL_REPORT.md').write_text('\n'.join(lines))
    # Reproducible artifact inventory, excluding itself and generated report figures.
    inventory = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(root.rglob('*')) if p.is_file() and p.name not in {'SHA256SUMS.json','RELEASE_AUDIT.json'}}
    (root/'SHA256SUMS.json').write_text(json.dumps(inventory,indent=2)+'\n')
    (root/'RELEASE_AUDIT.json').write_text(json.dumps({'passed':True,'traces':traces,'reuse_nodes':dict(reuse_count),
        'standard_rows':476,'drift_recovery_rows':32,'drift_control_traces':16},indent=2)+'\n')
    print(f'PASS: {traces} complete traces; wrote FINAL_REPORT.md and SHA256SUMS.json')


if __name__=='__main__':
    main()
