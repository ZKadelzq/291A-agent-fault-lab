"""Repair the current account by exact deltas, without pretending to rerun the plan."""
from .state import require


class Reconcile:
    name = 'reconcile'

    def build(self, ctx, runtime):
        runtime.prepare()
        w, target = runtime.world, runtime.target
        runtime.observe('read_account', {})
        if ctx.task.client_id not in w.state['kyc_verified']:
            runtime.compensate('verify_kyc', {'client_id': ctx.task.client_id}, 'verify identity before repair')
        for ticker, held in list(w.state['positions'].items()):
            excess = held - target['positions'].get(ticker, 0)
            if excess > 0:
                runtime.compensate('sell', {'ticker': ticker, 'shares': excess}, 'remove excess position')
        for ticker, wanted in target['positions'].items():
            missing = wanted - w.state['positions'].get(ticker, 0)
            if missing > 0:
                amount = round(missing * runtime.prices[ticker], 8)
                require(amount <= w.state['cash'] + 1e-6, 'INSUFFICIENT_REPAIR_CASH', False)
                runtime.compensate('buy', {'ticker': ticker, 'amount_usd': amount}, 'fill missing position')
        for ticker in list(w.state['stop_loss']):
            if ticker not in target['stop_loss']:
                runtime.compensate('clear_stop_loss', {'ticker': ticker}, 'remove obsolete stop-loss')
        for ticker, pct in target['stop_loss'].items():
            if w.state['stop_loss'].get(ticker) != pct:
                runtime.compensate('set_stop_loss', {'ticker': ticker, 'pct': pct}, 'set target stop-loss')
        account = runtime.observe('read_account', {})
        # Describe actual final holdings, not the abandoned plan or gross repair trades.
        content = 'Final holdings: ' + ', '.join(f'{t}: {n} shares' for t, n in sorted(account['positions'].items()))
        content += f". Cash: ${account['cash']:.2f}."
        runtime.compensate('write_file', {'path': ctx.task.report_path, 'content': content}, 'report actual account')
        return runtime.candidate()
