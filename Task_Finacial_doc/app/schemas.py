from pydantic import BaseModel, EmailStr
from typing import List
from datetime import datetime


# Request model for user registration
class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str


# Response model returned after user creation
class UserOut(BaseModel):
    id: int
    username: str
    email: str
    created_at: datetime

    class Config:
        from_attributes = True


# JWT token response model
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# Request model for creating roles
class RoleCreate(BaseModel):
    name: str
    permissions: List[str]


# Response model for role details
class RoleOut(BaseModel):
    id: int
    name: str
    permissions: List[str]

    class Config:
        from_attributes = True


# Request model for assigning a role to a user
class AssignRoleRequest(BaseModel):
    user_id: int
    role_name: str


# Response model for document metadata
class DocumentOut(BaseModel):
    id: int
    title: str
    company_name: str
    document_type: str
    file_path: str
    uploaded_by: int
    created_at: datetime

    class Config:
        from_attributes = True


# Metadata search request
class DocumentSearchQuery(BaseModel):
    query: str


# Semantic search request
class RagSearchRequest(BaseModel):
    query: str


# Semantic search response
class RagSearchResult(BaseModel):
    document_id: int
    chunk_index: int
    chunk_text: str
    score: float
    rerank_score: float