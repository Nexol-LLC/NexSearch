use crate::error::{ApiError, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::{PgPool, Postgres, Transaction};
use uuid::Uuid;

pub type Tx = Transaction<'static, Postgres>;
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Employee {
    pub id: Uuid,
    pub login: String,
    pub first_name: String,
    pub last_name: String,
    pub is_admin: bool,
    pub active: bool,
    pub role: String,
    pub requested_role: String,
    pub department: String,
    pub approved: bool,
}
impl Employee {
    pub fn leader(&self) -> bool {
        matches!(self.role.as_str(), "manager" | "director")
    }
    pub fn manages(&self, other: &Self) -> bool {
        self.id == other.id
            || self.role == "director"
            || (self.role == "manager"
                && other.role == "logist"
                && self.department == other.department)
    }
    pub fn from_row(mut value: Value) -> Result<Self> {
        let object = value.as_object_mut().ok_or_else(ApiError::missing)?;
        object.remove("password_hash");
        if object.get("is_admin").and_then(Value::as_bool) == Some(true) {
            object.insert("role".into(), "director".into());
            object.insert("approved".into(), true.into());
        }
        serde_json::from_value(value).map_err(|_| ApiError::missing())
    }
}
pub async fn transaction(pool: &PgPool, actor: Option<&Employee>, admin: bool) -> Result<Tx> {
    let mut tx = pool.begin().await?;
    sqlx::query("SELECT set_config('nexol.employee_id',$1,true),set_config('nexol.is_admin',$2,true),set_config('nexol.role',$3,true),set_config('nexol.department',$4,true),set_config('statement_timeout','20000',true),set_config('lock_timeout','4000',true)")
        .bind(actor.map(|a|a.id.to_string()).unwrap_or_default()).bind(if admin {"true"} else {"false"})
        .bind(actor.map(|a|a.role.as_str()).unwrap_or("")).bind(actor.map(|a|a.department.as_str()).unwrap_or("main"))
        .execute(&mut *tx).await?;
    Ok(tx)
}
pub async fn employee(tx: &mut Tx, id: Uuid) -> Result<Employee> {
    let value: Value = sqlx::query_scalar("SELECT to_jsonb(e) FROM employees e WHERE id=$1")
        .bind(id)
        .fetch_one(&mut **tx)
        .await?;
    Employee::from_row(value)
}
pub async fn audit(
    tx: &mut Tx,
    actor: Uuid,
    kind: &str,
    id: &str,
    details: Value,
    ip: &str,
) -> Result<()> {
    sqlx::query(
        "INSERT INTO audit_events(employee_id,kind,entity_id,details,ip) VALUES($1,$2,$3,$4,$5)",
    )
    .bind(actor)
    .bind(kind)
    .bind(id)
    .bind(details)
    .bind(ip)
    .execute(&mut **tx)
    .await?;
    Ok(())
}
pub async fn owned(tx: &mut Tx, actor: &Employee, company: Uuid, write: bool) -> Result<Uuid> {
    let owner: Uuid =
        sqlx::query_scalar("SELECT employee_id FROM employee_leads WHERE company_id=$1")
            .bind(company)
            .fetch_one(&mut **tx)
            .await?;
    if write && owner != actor.id {
        return Err(ApiError::denied(
            "Изменять контакты и статус может ответственный сотрудник",
        ));
    }
    Ok(owner)
}
pub fn id(value: &Value, key: &str) -> Result<Uuid> {
    Uuid::parse_str(value[key].as_str().unwrap_or(""))
        .map_err(|_| ApiError::invalid(format!("Некорректное поле: {key}")))
}
pub fn optional_id(value: &Value, key: &str) -> Result<Option<Uuid>> {
    if value[key].is_null() || value[key] == "" {
        Ok(None)
    } else {
        id(value, key).map(Some)
    }
}
pub fn text(value: &Value, key: &str, max: usize, required: bool) -> Result<String> {
    let s = value[key].as_str().unwrap_or("").trim();
    if (required && s.is_empty()) || s.chars().count() > max || s.contains('\0') {
        return Err(ApiError::invalid(format!("Проверьте поле: {key}")));
    }
    Ok(s.into())
}
pub fn revision(value: &Value) -> Result<i32> {
    value["revision"]
        .as_i64()
        .filter(|v| *v >= 0 && *v <= i32::MAX as i64)
        .map(|v| v as i32)
        .ok_or_else(|| ApiError::invalid("Нужна версия записи"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn role_scope_does_not_grant_manager_other_departments_or_leaders() {
        let mut a = Employee {
            id: Uuid::new_v4(),
            login: "a".into(),
            first_name: "А".into(),
            last_name: "Б".into(),
            is_admin: false,
            active: true,
            role: "manager".into(),
            requested_role: "manager".into(),
            department: "one".into(),
            approved: true,
        };
        let mut b = a.clone();
        b.id = Uuid::new_v4();
        b.role = "logist".into();
        assert!(a.manages(&b));
        b.department = "two".into();
        assert!(!a.manages(&b));
        b.department = "one".into();
        b.role = "manager".into();
        assert!(!a.manages(&b));
        a.role = "director".into();
        assert!(a.manages(&b));
        assert!(revision(&json!({"revision":-1})).is_err());
        assert!(text(&json!({"name":""}), "name", 200, true).is_err());
    }
}
