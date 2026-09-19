"""Persisted planning workflow, isolated from stateless research APIs."""
from datetime import datetime
import threading

from wildfire_data.planning.contracts import Definition, LIMITATIONS, canonical, engine_identity, now
from wildfire_data.planning.packs import validate_snapshot
from wildfire_data.planning.store import Conflict
from wildfire_data.planning.worker import Worker


class Service:
    def __init__(self, store, packs):
        self.store, self.packs = store, packs
        self.worker = Worker(store)
        self.lock = threading.RLock()

    def compatibility(self, scenario):
        definition = Definition.model_validate(scenario['definition'])
        if definition.engine_version != engine_identity():
            return 'Engine version differs. Saved results remain viewable/exportable.'
        if scenario['imported']:
            return 'Imported results are unverified and view-only. Clone to recompute with installed resources.'
        try:
            self.packs.require(definition, verify=True)
        except (ValueError, OSError) as exc:
            return str(exc)
        return None

    def get(self, identity):
        result = self.store.get(identity)
        result.update(continuation_blocked=self.compatibility(result), limitations=LIMITATIONS, playback_paused=True)
        return result

    def create(self, request):
        spec = request.definition
        _, snapshot = self.packs.require(spec, verify=True)
        if spec.engine_version != engine_identity():
            raise ValueError('New scenarios require the installed engine version')
        result, _ = self.store.mutate(request.request_id, 'create', request.model_dump(mode='json'),
            lambda: self.store.insert(request.name, spec.model_dump(mode='json'), snapshot))
        return self.get(result['id'])

    def update(self, identity, request):
        def change():
            previous = self.store.require_revision(identity, request.expected_revision)
            if previous['checkpoint'] >= 0 or previous['status'] == 'running' or previous['imported']:
                raise Conflict('Computed/imported cases are immutable. Clone this case to change assumptions.')
            if self.compatibility(previous):
                raise Conflict(self.compatibility(previous))
            if request.definition.pack_digest != previous['definition']['pack_digest'] or request.definition.pack_id != previous['definition']['pack_id']:
                raise ValueError('Create a new case to use another pack')
            if request.definition.engine_version != engine_identity():
                raise ValueError('Engine version mismatch')
            self.store.change(identity, name=request.name, definition=canonical(request.definition.model_dump(mode='json')))
            return identity
        result, _ = self.store.mutate(request.request_id, 'update', {'id': identity, **request.model_dump(mode='json')}, change)
        return self.get(result['id'])

    def clone(self, identity, request):
        def clone():
            previous = self.store.require_revision(identity, request.expected_revision)
            spec = Definition.model_validate(previous['definition'])
            _, snapshot = self.packs.require(spec, verify=True)
            # Never silently migrate the engine or policy in a clone.
            if spec.engine_version != engine_identity():
                raise Conflict('Install the compatible engine before cloning this case')
            return self.store.insert(request.name, previous['definition'], snapshot, parent=identity)
        result, _ = self.store.mutate(request.request_id, 'clone', {'id': identity, **request.model_dump(mode='json')}, clone)
        return self.get(result['id'])

    def run(self, identity, request):
        with self.lock:
            def begin():
                previous = self.store.require_revision(identity, request.expected_revision)
                blocked = self.compatibility(previous)
                if blocked:
                    raise Conflict(blocked)
                spec = Definition.model_validate(previous['definition'])
                if not spec.ignitions:
                    raise ValueError('Place at least one manual ignition on supported vegetation')
                if not self.worker.available() or previous['status'] == 'running':
                    raise Conflict('Only one calculation can run. Pause the active case first.')
                if previous['checkpoint'] >= spec.horizon_hours:
                    raise Conflict('This case has completed. Clone it for a new run.')
                self.store.change(identity, status='running', generation=previous['generation']+1, error=None)
                return identity
            result, fresh = self.store.mutate(request.request_id, 'run', {'id': identity, **request.model_dump(mode='json')}, begin)
            if fresh:
                try:
                    sampler, _ = self.packs.require(Definition.model_validate(result['definition']), verify=True)
                    self.worker.start(result, sampler.path)
                except Exception as exc:
                    self.store.finish(identity, result['generation'], str(exc))
                    raise
            return self.get(identity)

    def pause(self, identity, request):
        with self.lock:
            def pause():
                previous = self.store.require_revision(identity, request.expected_revision)
                self.store.change(identity, status='paused', generation=previous['generation']+1)
                return identity
            result, fresh = self.store.mutate(request.request_id, 'pause', {'id': identity, **request.model_dump(mode='json')}, pause)
            if fresh:
                self.worker.cancel(identity)
            return self.get(result['id'])

    def cursor(self, identity, request):
        def cursor():
            previous = self.store.require_revision(identity, request.expected_revision)
            if request.playback_hour > max(0, previous['checkpoint']):
                raise ValueError('Playback cannot exceed the last committed frame')
            self.store.change(identity, playback_hour=request.playback_hour)
            return identity
        result, _ = self.store.mutate(request.request_id, 'cursor', {'id': identity, **request.model_dump(mode='json')}, cursor)
        return self.get(result['id'])

    def import_case(self, request_id, portable):
        document = portable.model_dump(mode='json')
        validate_snapshot(document['pack_snapshot'], portable.definition)
        # Historical snapshots are data only; never installed as trusted packs.
        # Imported frames are labelled unverified, even if identities match.
        result, _ = self.store.mutate(request_id, 'import', document,
            lambda: self.store.insert(portable.name, document['definition'], document['pack_snapshot'],
                parent=str(portable.id), frames=document['frames'], cursor=portable.playback_hour,
                imported=True, created_at=document['created_at']))
        return self.get(result['id'])

    def document(self, identity):
        # Lock snapshot+frames together against a concurrent frame commit.
        with self.store.lock:
            scenario = self.store.get(identity)
            return {'schema_version': 1, 'kind': 'wildfire-planning-scenario', 'id': identity,
                'revision': scenario['revision'], 'parent_id': scenario['parent_id'], 'name': scenario['name'],
                'definition': scenario['definition'], 'pack_snapshot': scenario['pack_snapshot'],
                'created_at': scenario['created_at'], 'exported_at': now(),
                'frames': self.store.frames(identity), 'playback_hour': scenario['playback_hour'], 'limitations': LIMITATIONS}

    def close(self):
        with self.lock:
            job = self.worker.job
            if job:
                with self.store.transaction():
                    previous = self.store.get(job['id'])
                    self.store.change(job['id'], status='paused', generation=previous['generation']+1)
                self.worker.cancel()
            self.store.close()
