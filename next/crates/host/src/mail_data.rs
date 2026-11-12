use crate::{
    Host, RequestContext,
    crm::path_id,
    db,
    error::{ApiError, Result},
    mail::Account,
    mail_content,
};
use serde_json::{Value, json};
use uuid::Uuid;
pub const MESSAGE: &str = "id,account_id,folder,uid,subject,sender,recipients,addresses,sent_at,seen,size_bytes,message_id,in_reply_to,reference_ids";
pub async fn account(host: &Host, owner: Uuid, id: Uuid) -> Result<Account> {
    let mut tx = db::transaction(&host.pool, None, true).await?;
    let (info,secret):(Value,Vec<u8>)=sqlx::query_as("SELECT to_jsonb(a)-'secret',secret FROM mail_accounts a WHERE id=$1 AND employee_id=$2 AND enabled").bind(id).bind(owner).fetch_one(&mut *tx).await?;
    tx.commit().await?;
    Ok(Account { info, secret })
}
pub async fn message(host: &Host, owner: Uuid, id: Uuid) -> Result<Value> {
    let mut tx = db::transaction(&host.pool, None, true).await?;
    let value: Value = sqlx::query_scalar(
        "SELECT to_jsonb(m) FROM mail_messages m WHERE id=$1 AND employee_id=$2",
    )
    .bind(id)
    .bind(owner)
    .fetch_one(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(value)
}
pub async fn accounts(c: &RequestContext<'_>) -> Result<Value> {
    let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
    let mut accounts:Vec<Value>=sqlx::query_scalar("SELECT to_jsonb(r) FROM(SELECT id,email,login,label,imap_host,imap_port,smtp_host,smtp_port,enabled,folders,settings,last_sync_at,last_error FROM mail_accounts WHERE employee_id=$1 ORDER BY created_at) r").bind(c.actor.id).fetch_all(&mut *tx).await?;
    for a in &mut accounts {
        let counts:Option<Value>=sqlx::query_scalar("SELECT jsonb_object_agg(folder,jsonb_build_object('total',total,'unread',unread)) FROM(SELECT folder,count(*) AS total,count(*) FILTER(WHERE NOT seen) AS unread FROM mail_messages WHERE employee_id=$1 AND account_id=$2 GROUP BY folder) r").bind(c.actor.id).bind(db::id(a,"id")?).fetch_one(&mut *tx).await?;
        a["folder_counts"] = counts.unwrap_or_else(|| json!({}));
    }
    tx.commit().await?;
    Ok(json!({"accounts":accounts}))
}
pub async fn messages(c: &RequestContext<'_>) -> Result<Value> {
    let owner = c.actor.id;
    let q = &c.query;
    let account_id = q
        .get("account_id")
        .filter(|s| !s.is_empty())
        .map(|s| path_id(s))
        .transpose()?;
    let company = q
        .get("company_id")
        .filter(|s| !s.is_empty())
        .map(|s| path_id(s))
        .transpose()?;
    let folder = q.get("folder").map(String::as_str).unwrap_or("INBOX");
    let unread = q.get("unread").is_some_and(|s| s == "true" || s == "1");
    let before = q
        .get("before")
        .map(|s| {
            chrono::DateTime::parse_from_rfc3339(s)
                .map(|v| v.with_timezone(&chrono::Utc))
                .map_err(|_| ApiError::invalid("Проверьте дату писем"))
        })
        .transpose()?;
    let before_uid = q
        .get("before_uid")
        .map(|s| {
            s.parse::<i64>()
                .map_err(|_| ApiError::invalid("Проверьте страницу писем"))
        })
        .transpose()?;
    let limit = q
        .get("limit")
        .and_then(|s| s.parse::<i64>().ok())
        .unwrap_or(100)
        .clamp(1, 500);
    let search = q
        .get("q")
        .map(|s| s.chars().take(200).collect::<String>())
        .unwrap_or_default();
    let mut tx = db::transaction(&c.host.pool, Some(c.actor), false).await?;
    let mut addresses: Vec<String> = vec![];
    if let Some(company) = company {
        db::owned(&mut tx, c.actor, company, false).await?;
        let payload: Value = sqlx::query_scalar("SELECT payload FROM companies WHERE id=$1")
            .bind(company)
            .fetch_one(&mut *tx)
            .await?;
        for values in [&payload["emails"], &payload["contact_overrides"]["emails"]] {
            for value in values
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
            {
                addresses.push(value.trim().to_lowercase())
            }
        }
        let extra: Vec<String> =
            sqlx::query_scalar("SELECT email FROM company_contacts WHERE company_id=$1")
                .bind(company)
                .fetch_all(&mut *tx)
                .await?;
        for value in extra {
            addresses.extend(
                value
                    .split([',', ';', '\n'])
                    .map(|s| s.trim().to_lowercase())
                    .filter(|s| s.contains('@')),
            )
        }
    }
    let sql = format!(
        "SELECT to_jsonb(r) FROM(SELECT {MESSAGE} FROM mail_messages WHERE employee_id=$1 AND (($2 AND addresses && $3) OR (NOT $2 AND ($4::uuid IS NULL OR account_id=$4) AND folder=$5)) AND (NOT $6 OR NOT seen) AND ($7::timestamptz IS NULL OR COALESCE(sent_at,cached_at)<$7) AND ($8::bigint IS NULL OR uid<$8) AND ($9='' OR subject ILIKE $9 OR sender ILIKE $9 OR body_text ILIKE $9) ORDER BY {} LIMIT $10) r",
        if account_id.is_some() && company.is_none() {
            "uid DESC"
        } else {
            "COALESCE(sent_at,cached_at) DESC,id"
        }
    );
    let search = if search.is_empty() {
        String::new()
    } else {
        format!("%{search}%")
    };
    let messages: Vec<Value> = sqlx::query_scalar(&sql)
        .bind(owner)
        .bind(company.is_some())
        .bind(addresses)
        .bind(account_id)
        .bind(folder)
        .bind(unread)
        .bind(before)
        .bind(before_uid)
        .bind(search)
        .bind(limit)
        .fetch_all(&mut *tx)
        .await?;
    let mut related = vec![];
    if let Some(account_id) = account_id
        && company.is_none()
        && !messages.is_empty()
    {
        let info: Value = sqlx::query_scalar(
            "SELECT to_jsonb(a)-'secret' FROM mail_accounts a WHERE id=$1 AND employee_id=$2",
        )
        .bind(account_id)
        .bind(owner)
        .fetch_one(&mut *tx)
        .await?;
        let own = info["email"].as_str().unwrap_or("").to_lowercase();
        let login = info["login"].as_str().unwrap_or("").to_lowercase();
        let mut partners = vec![];
        for m in &messages {
            for address in m["addresses"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
            {
                if address != own
                    && address != login
                    && !partners.iter().any(|p: &String| p == address)
                {
                    partners.push(address.to_owned())
                }
            }
        }
        partners.truncate(200);
        let sent: Vec<String> = info["folders"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|f| f["sent"] == true)
            .filter_map(|f| f["id"].as_str().map(str::to_owned))
            .collect();
        let targets = if sent.iter().any(|s| s == folder) {
            vec!["INBOX".into()]
        } else {
            sent
        };
        if !partners.is_empty() && !targets.is_empty() {
            related=sqlx::query_scalar::<_,Value>(&format!("SELECT to_jsonb(r) FROM(SELECT {MESSAGE} FROM mail_messages WHERE employee_id=$1 AND account_id=$2 AND folder=ANY($3) AND addresses && $4 ORDER BY COALESCE(sent_at,cached_at) DESC,id LIMIT 500) r")).bind(owner).bind(account_id).bind(targets).bind(partners).fetch_all(&mut *tx).await?;
        }
    }
    tx.commit().await?;
    Ok(json!({"messages":messages,"related":related}))
}
pub fn response(mut row: Value, account: &Account, warning: &str) -> Value {
    if let Some(obj) = row.as_object_mut() {
        for key in ["employee_id", "uid", "uidvalidity"] {
            obj.remove(key);
        }
    }
    row["remote_image_count"] = mail_content::image_urls(row["body_html"].as_str().unwrap_or(""))
        .len()
        .into();
    row["warning"] = warning.into();
    row["read_mode"] = account.info["settings"]["read_mode"]
        .as_str()
        .unwrap_or("auto")
        .into();
    row
}
