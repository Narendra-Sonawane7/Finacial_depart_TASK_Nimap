import json
import os
import shutil
from datetime import timedelta

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .models import User, Role, Document, DocumentChunk
from .schemas import (
    UserCreate, UserOut, Token, RoleCreate, RoleOut, AssignRoleRequest,
    DocumentOut, RagSearchRequest, RagSearchResult
)
from .auth import hash_password, create_access_token, authenticate_user, get_current_user
from .rbac import get_user_permissions, has_permission
from .rag import rag_store, extract_text_from_file, chunk_text

app = FastAPI(title="Financial Document Management API")

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def seed_default_roles(db: Session):
    default_roles = {
        "Admin": ["full_access"],
        "Financial Analyst": ["upload_documents", "edit_documents", "search_documents"],
        "Auditor": ["review_documents", "search_documents", "view_documents"],
        "Client": ["view_company_documents"],
    }

    for role_name, permissions in default_roles.items():
        role = db.query(Role).filter(Role.name == role_name).first()
        if not role:
            db.add(Role(name=role_name, permissions=json.dumps(permissions)))
    db.commit()


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    seed_default_roles(db)
    db.close()


@app.post("/auth/register", response_model=UserOut)
def register(user: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(
        (User.username == user.username) | (User.email == user.email)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username or email already exists")

    new_user = User(
        username=user.username,
        email=user.email,
        hashed_password=hash_password(user.password),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # First user becomes Admin
    users_count = db.query(User).count()
    if users_count == 1:
        admin_role = db.query(Role).filter(Role.name == "Admin").first()
        if admin_role:
            new_user.roles.append(admin_role)
            db.commit()

    return new_user


@app.post("/auth/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token(data={"sub": user.username}, expires_delta=timedelta(days=1))
    return {"access_token": token, "token_type": "bearer"}


@app.post("/roles/create", response_model=RoleOut)
def create_role(
    role: RoleCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not has_permission(current_user, "full_access"):
        raise HTTPException(status_code=403, detail="Only admin can create roles")

    existing = db.query(Role).filter(Role.name == role.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Role already exists")

    new_role = Role(name=role.name, permissions=json.dumps(role.permissions))
    db.add(new_role)
    db.commit()
    db.refresh(new_role)
    return new_role


@app.post("/users/assign-role")
def assign_role(
    payload: AssignRoleRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not has_permission(current_user, "full_access"):
        raise HTTPException(status_code=403, detail="Only admin can assign roles")

    user = db.query(User).filter(User.id == payload.user_id).first()
    role = db.query(Role).filter(Role.name == payload.role_name).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    if role not in user.roles:
        user.roles.append(role)
        db.commit()

    return {"message": f"Role '{payload.role_name}' assigned to user '{user.username}'"}


@app.get("/users/{user_id}/roles")
def get_user_roles(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "user_id": user.id,
        "username": user.username,
        "roles": [r.name for r in user.roles],
    }


@app.get("/users/{user_id}/permissions")
def get_user_permissions_endpoint(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "user_id": user.id,
        "username": user.username,
        "permissions": get_user_permissions(user),
    }


@app.post("/documents/upload", response_model=DocumentOut)
def upload_document(
    title: str = Form(...),
    company_name: str = Form(...),
    document_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not has_permission(current_user, "upload_documents") and not has_permission(current_user, "full_access"):
        raise HTTPException(status_code=403, detail="You do not have permission to upload documents")

    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    doc = Document(
        title=title,
        company_name=company_name,
        document_type=document_type,
        file_path=file_path,
        uploaded_by=current_user.id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

           # Index immediately after upload
    try:
        text = extract_text_from_file(file_path)
        chunks = chunk_text(text)
        rag_store.index_document(doc.id, chunks)

        for idx, chunk in enumerate(chunks):
            db.add(DocumentChunk(document_id=doc.id, chunk_index=idx, chunk_text=chunk))
        db.commit()
    except Exception:
            # Keep the document even if indexing fails
        pass

    return doc


@app.get("/documents", response_model=list[DocumentOut])
def get_documents(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return db.query(Document).order_by(Document.created_at.desc()).all()


@app.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@app.delete("/documents/{document_id}")
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not has_permission(current_user, "edit_documents") and not has_permission(current_user, "full_access"):
        raise HTTPException(status_code=403, detail="You do not have permission to delete documents")

    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    rag_store.remove_document(document_id)
    db.delete(doc)
    db.commit()

    return {"message": "Document deleted successfully"}


@app.get("/documents/search", response_model=list[DocumentOut])
def search_documents(
    title: str | None = None,
    company_name: str | None = None,
    document_type: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(Document)

    if title:
        query = query.filter(Document.title.ilike(f"%{title}%"))
    if company_name:
        query = query.filter(Document.company_name.ilike(f"%{company_name}%"))
    if document_type:
        query = query.filter(Document.document_type.ilike(f"%{document_type}%"))

    return query.order_by(Document.created_at.desc()).all()


@app.post("/rag/index-document")
def index_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    text = extract_text_from_file(doc.file_path)
    chunks = chunk_text(text)

    db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete()
    for idx, chunk in enumerate(chunks):
        db.add(DocumentChunk(document_id=document_id, chunk_index=idx, chunk_text=chunk))
    db.commit()

    rag_store.index_document(document_id, chunks)
    return {"message": "Document indexed successfully", "chunks": len(chunks)}


@app.delete("/rag/remove-document/{document_id}")
def remove_document_embeddings(
    document_id: int,
    current_user=Depends(get_current_user),
):
    rag_store.remove_document(document_id)
    return {"message": "Document embeddings removed successfully"}


@app.post("/rag/search", response_model=list[RagSearchResult])
def rag_search(
    payload: RagSearchRequest,
    current_user=Depends(get_current_user),
):
    results = rag_store.search(payload.query, top_k=5)
    return results


@app.get("/rag/context/{document_id}")
def rag_context(
    document_id: int,
    current_user=Depends(get_current_user),
):
    context = rag_store.get_context(document_id)
    if not context:
        raise HTTPException(status_code=404, detail="No context found for this document")
    return {"document_id": document_id, "context": context}