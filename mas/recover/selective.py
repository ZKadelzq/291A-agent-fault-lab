"""Conservative recovery: retain research, or a validated pre-fault execution prefix."""
from .state import valid_prefix


class Restart:
    name = 'restart'

    def build(self, ctx, runtime):
        runtime.prepare(restart=True)
        runtime.rerun_suffix(0)
        return runtime.candidate()


class Selective:
    name = 'selective'

    def build(self, ctx, runtime):
        runtime.prepare()
        target = ctx.old_trace.nodes[ctx.target]
        prefix = 0
        if target.agent == 'executor':
            index = min(int(n.split(':')[1]) for n in runtime.invalid if n.startswith('exec:'))
            prefix = valid_prefix(ctx.old_trace, runtime.steps, runtime.world, index)
        runtime.rerun_suffix(prefix)
        return runtime.candidate()
