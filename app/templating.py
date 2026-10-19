from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def render(request: Request, name: str, status_code: int = 200, **context):
    """Rend un template en injectant le nonce CSP et le jeton CSRF de la requête."""
    context.setdefault("csp_nonce", getattr(request.state, "csp_nonce", ""))
    context.setdefault("csrf_token", getattr(request.state, "csrf_token", ""))
    return templates.TemplateResponse(request, name, context, status_code=status_code)
