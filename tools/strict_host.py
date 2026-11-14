"""Trusted controller: official HostedLearner with process-isolated fresh resets.

Frozen resets use the upstream implementation unchanged. Fresh references also
restart the process and remove its owned state/home trees, so their isolation
does not depend on a learner correctly implementing reset().
"""
import datetime
import hashlib
import json
import os
import time

from continual_arc_baselines.systems.hosted import HostedLearner, WRITABLE
from continual_arc_baselines.frozen import delete
from reset_audit import snapshot_digest


class StrictHostedLearner(HostedLearner):
    def __init__(self, *args, reset_reference=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.reset_reference = reset_reference
        self._restore_id = 0
        self._consumed_restore_id = 0
        self._last_restore = None
        self._active_instance = None
        self._snapshot_audit_object = None
        self._snapshot_audit_sha256 = None

    def _record_reset(self, **event):
        # This file is outside WRITABLE and outside the child's mounted paths.
        event.update(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     controller_stage=os.environ.get('CT_CONTROLLER_STAGE_ID'),
                     audit_version=1)
        path = self.workdir / 'reset-audit.jsonl'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                     | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'a', encoding='utf8') as handle:
            handle.write(json.dumps(event, separators=(',', ':')) + '\n')

    def _start(self):
        if self.frozen is not None:
            started = time.monotonic()
            expected = snapshot_digest(self.frozen)
            actual = snapshot_digest(self.workdir)
            if self._snapshot_audit_object is not self.frozen:
                self._snapshot_audit_object = self.frozen
                self._snapshot_audit_sha256 = expected['sha256']
            matched = actual['sha256'] == expected['sha256'] == self._snapshot_audit_sha256
            self._restore_id += 1
            self._last_restore = {'mode': 'frozen', 'restore_id': self._restore_id}
            self._record_reset(event='frozen_restore', **self._last_restore,
                               expected=expected, actual=actual, matched=matched,
                               audit_seconds=time.monotonic() - started,
                               learner_process_running=False)
            if not matched:
                raise RuntimeError('Frozen writable snapshot mismatch before learner start')
        self._active_instance = None
        return super()._start()

    def act(self, obs):
        mode = 'frozen' if self.frozen is not None else 'fresh' if self.reset_reference else None
        key = (obs.user, obs.step, obs.task_id)
        if mode is not None and key != self._active_instance:
            restore = self._last_restore
            if (restore is None or restore['mode'] != mode
                    or restore['restore_id'] <= self._consumed_restore_id):
                raise RuntimeError('A controlled instance has no new verified reset')
            self._consumed_restore_id = restore['restore_id']
            self._active_instance = key
            self._record_reset(event='instance_begin', **restore, step=obs.step,
                               instance_key_sha256=hashlib.sha256(json.dumps(key).encode()).hexdigest())
        return super().act(obs)

    def reset(self):
        if self.frozen is not None:
            return super().reset()
        previous_pid = self._proc.pid if self._proc is not None else None
        self._end_host()
        base=self.workdir.resolve()
        for name in WRITABLE:
            path=self.workdir/name
            if path.resolve().parent != base:
                raise RuntimeError('Refusing reset outside this attempt')
            if path.exists():delete(path)
        if any((self.workdir / name).exists() for name in WRITABLE):
            raise RuntimeError('Fresh reset left an owned writable tree behind')
        self._start()
        if self._round is not None:
            self._call('begin_round',n=self._round)
        self._restore_id += 1
        self._last_restore = {'mode': 'fresh', 'restore_id': self._restore_id}
        self._record_reset(event='fresh_reset', **self._last_restore,
                           all_writable_removed_before_start=True, previous_pid=previous_pid,
                           process_pid=self._proc.pid, process_restarted=self._proc.pid != previous_pid)
