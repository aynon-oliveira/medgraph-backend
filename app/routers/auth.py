from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_current_user_optional
from app.core.limitador import limitador_login
from app.core.security import criar_token, hash_senha, verificar_senha
from app.models import Perfil, Usuario
from app.schemas.usuario import MensagemOut, Token, TrocarSenhaIn, UsuarioCreate, UsuarioOut, problemas_da_senha

router = APIRouter(prefix="/auth", tags=["Autenticação"])


@router.post("/registrar", response_model=UsuarioOut, status_code=status.HTTP_201_CREATED)
def registrar(
    dados: UsuarioCreate,
    db: Session = Depends(get_db),
    atual: Usuario | None = Depends(get_current_user_optional),
):
    """Cadastra um usuário.

    - Se o banco estiver vazio, o PRIMEIRO cadastro é livre, mas precisa ser GESTOR.
    - Depois disso, só um GESTOR logado pode cadastrar novos usuários.
    """
    total = db.scalar(select(func.count()).select_from(Usuario))

    if total == 0:
        if dados.perfil != Perfil.GESTOR:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="O primeiro usuário do sistema deve ter o perfil GESTOR.",
            )
    else:
        if atual is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Faça login como GESTOR para cadastrar usuários.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if atual.perfil != Perfil.GESTOR:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Somente o GESTOR pode cadastrar usuários.",
            )

    if db.scalar(select(Usuario).where(Usuario.email == dados.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado.")
    if dados.cpf and db.scalar(select(Usuario).where(Usuario.cpf == dados.cpf)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="CPF já cadastrado.")

    usuario = Usuario(
        nome=dados.nome,
        email=dados.email,
        senha_hash=hash_senha(dados.senha),
        perfil=dados.perfil,
        cpf=dados.cpf,
        microarea=dados.microarea,
        crm=dados.crm,
        departamento=dados.departamento,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Login. No campo `username` vai o e-mail. Devolve o token JWT."""
    chave = limitador_login.chave(form.username)
    espera = limitador_login.segundos_bloqueado(chave)
    if espera > 0:
        minutos = max(1, (espera + 59) // 60)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Muitas tentativas de entrada. Tente de novo em {minutos} minuto(s).",
            headers={"Retry-After": str(espera)},
        )
    usuario = db.scalar(select(Usuario).where(Usuario.email == form.username.strip().lower()))
    if (
        usuario is None
        or not usuario.ativo
        or not verificar_senha(form.password, usuario.senha_hash)
    ):
        limitador_login.registrar_falha(chave)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha incorretos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    limitador_login.registrar_sucesso(chave)
    return Token(access_token=criar_token(usuario.id, usuario.perfil), perfil=usuario.perfil)


@router.get("/me", response_model=UsuarioOut)
def me(usuario: Usuario = Depends(get_current_user)):
    """Dados de quem está logado."""
    return usuario


@router.post("/trocar-senha", response_model=MensagemOut)
def trocar_senha(
    dados: TrocarSenhaIn,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """O próprio usuário troca a senha. Depois da troca, TODOS os tokens antigos deixam de valer
    (inclusive o desta chamada): é preciso entrar de novo."""
    # 400 (e não 401) para a tela não confundir "senha atual errada" com "sessão expirada"
    if not verificar_senha(dados.senha_atual, usuario.senha_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Senha atual incorreta.")
    if dados.senha_nova == dados.senha_atual:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A senha nova deve ser diferente da atual.")
    problemas = problemas_da_senha(dados.senha_nova, usuario.email, usuario.nome)
    if problemas:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=" ".join(problemas))

    usuario.senha_hash = hash_senha(dados.senha_nova)
    usuario.senha_alterada_em = datetime.now(timezone.utc)
    db.commit()
    return MensagemOut(detail="Senha alterada. Entre novamente com a senha nova.")
