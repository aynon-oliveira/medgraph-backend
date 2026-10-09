from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.deps import get_current_user
from app.models import Usuario
from app.services import ia_risco

router = APIRouter(prefix="/api/v1/ia", tags=["IA de triagem"])


@router.get("/modelo")
def baixar_modelo(resposta: Response, _: Usuario = Depends(get_current_user)):
    """Entrega o modelo de risco (coeficientes) para o site calcular a pontuação OFFLINE.

    Só tem números (nenhum dado de paciente). Qualquer usuário autenticado pode baixar.
    404 enquanto o modelo ainda não foi treinado: o site segue com a triagem por palavras-chave.
    """
    modelo = ia_risco.carregar()
    if modelo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="O modelo de risco ainda não foi treinado neste servidor.")
    resposta.headers["Cache-Control"] = "no-cache"
    return ia_risco.informacao_publica(modelo)
