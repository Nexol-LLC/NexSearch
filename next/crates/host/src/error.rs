use axum::{
    Json,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde_json::json;

pub type Result<T> = std::result::Result<T, ApiError>;
#[derive(Debug)]
pub struct ApiError(pub StatusCode, pub String);
impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.1)
    }
}
impl std::error::Error for ApiError {}
impl ApiError {
    pub fn invalid(message: impl Into<String>) -> Self {
        Self(StatusCode::UNPROCESSABLE_ENTITY, message.into())
    }
    pub fn denied(message: impl Into<String>) -> Self {
        Self(StatusCode::FORBIDDEN, message.into())
    }
    pub fn missing() -> Self {
        Self(StatusCode::NOT_FOUND, "Запись недоступна".into())
    }
    pub fn conflict(message: impl Into<String>) -> Self {
        Self(StatusCode::CONFLICT, message.into())
    }
    pub fn unauthorized() -> Self {
        Self(
            StatusCode::UNAUTHORIZED,
            "Сеанс завершён. Войдите снова.".into(),
        )
    }
}
impl From<sqlx::Error> for ApiError {
    fn from(error: sqlx::Error) -> Self {
        if let sqlx::Error::Database(ref db) = error {
            if db.is_unique_violation() {
                return Self::conflict("Запись уже существует или операция уже выполняется");
            }
            if db.is_foreign_key_violation() {
                return Self::invalid("Связанная запись недоступна");
            }
            if db.code().as_deref() == Some("42501") {
                return Self::denied("Недостаточно прав для операции");
            }
        }
        if matches!(error, sqlx::Error::RowNotFound) {
            return Self::missing();
        }
        // Never return database URLs, SQL, parameters or server internals to a client.
        tracing::error!(kind=?std::mem::discriminant(&error), "Ошибка операции PostgreSQL");
        Self(
            StatusCode::SERVICE_UNAVAILABLE,
            "Операция с базой не завершена. Обновите данные перед повтором.".into(),
        )
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error":self.1}))).into_response()
    }
}
