"""Change pruning at a verified contiguous execution prefix; never arbitrary splicing."""
class ChangeAware:
    name = 'change_aware'

    def build(self, ctx, runtime):
        runtime.prepare()
        limit = len(runtime.steps)
        node = ctx.old_trace.nodes[ctx.target]
        if node.agent == 'executor':
            limit = min(limit, int(node.id.split(':')[1]))
        prefix = runtime.prefix(limit)
        runtime.rerun_suffix(prefix)
        return runtime.candidate()
