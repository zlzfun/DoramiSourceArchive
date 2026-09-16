"""Keep cached pathnames alive throughout Starlette's deferred file response."""

from fastapi.responses import FileResponse, JSONResponse
from services.object_storage import ObjectStorageError
from starlette.concurrency import run_in_threadpool


class StorageFileResponse(FileResponse):
    def __init__(self, store, record, **kwargs):
        self.store = store
        self.record = record
        super().__init__(store.file_path_for(record), **kwargs)

    async def __call__(self, scope, receive, send):
        storage = getattr(self.store, "object_storage", None)
        if storage is None or not storage.enabled:
            return await super().__call__(scope, receive, send)
        lease = storage.pin(self.record.content_hash)
        await run_in_threadpool(lease.__enter__)
        try:
            # A cache worker may have run after the route prepared this response.
            try:
                await run_in_threadpool(self.store.readable_path, self.record)
            except ObjectStorageError:
                return await JSONResponse(
                    {"detail": "媒体暂时不可用，请稍后重试"},
                    status_code=503, headers={"Retry-After": "30", "Cache-Control": "no-store"},
                )(scope, receive, send)
            # pathsend may outlive this call in the server; use Starlette's own
            # chunked file path while the lease is held, including Range/HEAD.
            scope = {**scope, "extensions": {key: value for key, value in scope.get("extensions", {}).items()
                                            if key != "http.response.pathsend"}}
            await super().__call__(scope, receive, send)
        finally:
            lease.__exit__(None, None, None)
