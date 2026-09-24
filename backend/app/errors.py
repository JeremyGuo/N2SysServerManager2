"""Safe, consistent API errors. Detailed exceptions stay in server logs."""
from uuid import uuid4

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException
from logger import logger


def install_error_handlers(app):
    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = uuid4().hex[:12]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def response(request, status, detail, headers=None):
        rid = getattr(request.state, "request_id", uuid4().hex[:12])
        return JSONResponse(
            status_code=status,
            content={"detail": detail, "request_id": rid},
            headers={**(headers or {}), "X-Request-ID": rid},
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return response(request, exc.status_code, exc.detail, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never echo the rejected input: it may contain a password or key.
        detail = [{"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]}
                  for err in exc.errors()]
        return response(request, 422, detail)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        logger.warning("Database constraint conflict request=%s", request.state.request_id)
        return response(request, 409, "数据冲突：记录已存在或关联对象已被删除。请刷新后检查用户名、邮箱及所选设备。")

    @app.exception_handler(OperationalError)
    async def database_error(request, exc):
        logger.error("Database unavailable request=%s", request.state.request_id)
        return response(request, 503, "数据库暂时不可用或正忙，请稍后重试；持续失败请让管理员检查数据库连接和磁盘空间。")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.exception("Unhandled request error request=%s", getattr(request.state, "request_id", "unknown"))
        return response(request, 500, "服务器处理请求时发生内部错误，操作未确认成功。请将请求编号交给管理员查询后端日志。")
