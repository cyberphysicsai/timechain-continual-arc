"""Trusted controller: official HostedLearner with process-isolated fresh resets.

Frozen resets use the upstream implementation unchanged. Fresh references also
restart the process and remove its owned state/home trees, so their isolation
does not depend on a learner correctly implementing reset().
"""
from continual_arc_baselines.systems.hosted import HostedLearner, WRITABLE
from continual_arc_baselines.frozen import delete


class StrictHostedLearner(HostedLearner):
    def reset(self):
        if self.frozen is not None:
            return super().reset()
        self._end_host()
        base=self.workdir.resolve()
        for name in WRITABLE:
            path=self.workdir/name
            if path.resolve().parent != base:
                raise RuntimeError('Refusing reset outside this attempt')
            if path.exists():delete(path)
        self._start()
        if self._round is not None:
            self._call('begin_round',n=self._round)
