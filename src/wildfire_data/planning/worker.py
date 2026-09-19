"""One spawned calculation process, bounded threads, one unacknowledged frame."""
import multiprocessing
import os
import threading


def calculate(connection, manifest, definition, start_hour):
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[key] = '2'
    os.environ['PROJ_NETWORK'] = 'OFF'
    from wildfire_data.planning.network import deny_outbound
    deny_outbound()
    try:
        from threadpoolctl import threadpool_limits
        from wildfire_data.planning.contracts import Definition
        from wildfire_data.model.features.fuel_barrier_features import FuelBarrierSampler
        from wildfire_data.model.local_spread import LocalSpreadModel
        spec = Definition.model_validate(definition)
        with threadpool_limits(limits=2):
            sampler = FuelBarrierSampler(manifest, expected_sha256=spec.pack_digest)
            model = LocalSpreadModel(sampler, spec.policy.travel(spec.wind), mesh_m=spec.mesh_m)
            seeds = model.seed_ids([(i.longitude, i.latitude) for i in spec.ignitions])
            for hour in range(start_hour, spec.horizon_hours + 1):
                connection.send(('frame', hour, model.frame(seeds, hour*60)))
                # Back-pressure prevents geometry frames accumulating in RAM.
                if connection.recv() != 'committed':
                    return
            connection.send(('complete',))
    except BaseException as exc:
        try:
            connection.send(('error', f'{type(exc).__name__}: {exc}'))
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


class Worker:
    def __init__(self, store):
        self.store = store
        self.job = None
        self.lock = threading.RLock()
        self.volatile_error = None

    def available(self):
        with self.lock:
            return self.job is None

    def start(self, scenario, manifest):
        with self.lock:
            if self.job is not None:
                raise ValueError('One calculation at a time; pause the active case first')
            context = multiprocessing.get_context('spawn')
            parent, child = context.Pipe()
            process = context.Process(target=calculate, args=(child, str(manifest), scenario['definition'], scenario['checkpoint']+1), daemon=True)
            job = {'process': process, 'pipe': parent, 'id': scenario['id'], 'generation': scenario['generation']}
            self.job = job
            try:
                process.start()
                child.close()
                job['thread'] = threading.Thread(target=self._collect, args=(job,), daemon=True)
                job['thread'].start()
            except BaseException:
                parent.close()
                child.close()
                self.job = None
                raise

    def _collect(self, job):
        pipe, process = job['pipe'], job['process']
        try:
            while True:
                if not pipe.poll(.2):
                    if not process.is_alive():
                        raise RuntimeError('Calculation process exited before completing; checkpoint retained')
                    continue
                message = pipe.recv()
                if message[0] == 'frame':
                    if not self.store.commit_frame(job['id'], job['generation'], message[1], message[2]):
                        break
                    pipe.send('committed')
                elif message[0] == 'complete':
                    self.store.finish(job['id'], job['generation'])
                    break
                else:
                    raise RuntimeError(message[1])
        except (Exception,) as exc:
            try:
                self.store.finish(job['id'], job['generation'], str(exc))
            except Exception as storage_exc:
                # Even if the disk cannot record the failure, it must be visible.
                self.volatile_error = f'Save failed: {storage_exc}. Last acknowledged frame retained.'
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            pipe.close()
            with self.lock:
                if self.job is job:
                    self.job = None

    def cancel(self, identity=None):
        with self.lock:
            job = self.job
            if not job or identity is not None and job['id'] != identity:
                return
            if job['process'].is_alive():
                job['process'].terminate()
        # The caller invalidates the DB generation BEFORE terminating. Joining
        # never holds the job mutex, which the collector needs during cleanup.
        job['thread'].join(timeout=10)
        if job['thread'].is_alive():
            raise RuntimeError('Calculation shutdown timed out; new work is blocked')
