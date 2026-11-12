use crate::{
    error::{ApiError, Result},
    mail_transport::{Part, Socket, error, quoted},
};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use regex::Regex;
use serde_json::{Value, json};
use std::sync::OnceLock;
pub fn encode(name: &str) -> String {
    let mut out = String::new();
    let mut run = String::new();
    let flush = |run: &mut String, out: &mut String| {
        if !run.is_empty() {
            let bytes: Vec<_> = run.encode_utf16().flat_map(u16::to_be_bytes).collect();
            out.push('&');
            out.push_str(
                &STANDARD
                    .encode(bytes)
                    .trim_end_matches('=')
                    .replace('/', ","),
            );
            out.push('-');
            run.clear();
        }
    };
    for c in name.chars() {
        if c.is_ascii() && !c.is_ascii_control() {
            flush(&mut run, &mut out);
            if c == '&' {
                out.push_str("&-")
            } else {
                out.push(c)
            }
        } else {
            run.push(c)
        }
    }
    flush(&mut run, &mut out);
    out
}
pub fn decode(raw: &str) -> String {
    let mut out = String::new();
    let mut rest = raw;
    while let Some(pos) = rest.find('&') {
        out.push_str(&rest[..pos]);
        rest = &rest[pos + 1..];
        let Some(end) = rest.find('-') else {
            out.push('&');
            out.push_str(rest);
            return out;
        };
        let part = &rest[..end];
        if part.is_empty() {
            out.push('&')
        } else {
            let mut data = part.replace(',', "/");
            while data.len() % 4 != 0 {
                data.push('=')
            }
            if let Ok(bytes) = STANDARD.decode(data) {
                let units: Vec<_> = bytes
                    .as_chunks::<2>()
                    .0
                    .iter()
                    .map(|b| u16::from_be_bytes([b[0], b[1]]))
                    .collect();
                out.push_str(&String::from_utf16_lossy(&units));
            } else {
                out.push_str(part)
            }
        }
        rest = &rest[end + 1..];
    }
    out.push_str(rest);
    out
}
pub fn parse_list(rows: &[Part]) -> Vec<Value> {
    static LIST: OnceLock<Regex> = OnceLock::new();
    let pattern = LIST.get_or_init(|| {
        Regex::new(r#"(?i)^\* LIST \(([^)]*)\) (NIL|"(?:\\.|[^"])*") (.+)\r?\n?$"#).unwrap()
    });
    let mut folders = vec![];
    for row in rows {
        let Some(c) = pattern.captures(&row.line) else {
            continue;
        };
        let flags: Vec<_> = c[1].split_whitespace().map(str::to_owned).collect();
        let selectable = !flags.iter().any(|v| v.eq_ignore_ascii_case("\\Noselect"));
        let separator = c[2].trim_matches('"').to_owned();
        let raw = if let Some(bytes) = &row.literal {
            String::from_utf8_lossy(bytes).into_owned()
        } else {
            c[3].trim()
                .trim_matches('"')
                .replace("\\\"", "\"")
                .replace("\\\\", "\\")
        };
        let raw = if raw.eq_ignore_ascii_case("INBOX") {
            "INBOX".to_owned()
        } else {
            raw
        };
        let name = decode(&raw);
        let lower = name.to_lowercase();
        let sent = flags.iter().any(|f| f.eq_ignore_ascii_case("\\Sent"))
            || ["sent", "отправлен", "отослан"]
                .iter()
                .any(|s| lower.contains(s));
        let quiet =
            sent || flags.iter().any(|f| {
                ["\\Junk", "\\Trash"]
                    .iter()
                    .any(|s| f.eq_ignore_ascii_case(s))
            }) || ["спам", "корзин", "trash", "junk"]
                .iter()
                .any(|s| lower.contains(s));
        folders.push(json!({"id":raw,"name":if raw=="INBOX"{"Входящие".into()}else{name},"delimiter":if separator=="NIL"{String::new()}else{separator},"sent":sent,"quiet":quiet,"flags":flags,"selectable":selectable}));
    }
    folders.sort_by(|a, b| {
        (a["id"] != "INBOX")
            .cmp(&(b["id"] != "INBOX"))
            .then_with(|| {
                a["name"]
                    .as_str()
                    .unwrap_or("")
                    .cmp(b["name"].as_str().unwrap_or(""))
            })
    });
    folders
}
pub async fn list(socket: &mut Socket) -> Result<Vec<Value>> {
    let rows = socket.command("LIST \"\" \"*\"", 1024 * 1024).await?;
    let folders = parse_list(&rows);
    if folders.is_empty() {
        return Err(error("Сервер не вернул доступных папок"));
    }
    Ok(folders)
}
pub async fn select(socket: &mut Socket, folder: &str, readonly: bool) -> Result<String> {
    let rows = socket
        .command(
            &format!(
                "{} {}",
                if readonly { "EXAMINE" } else { "SELECT" },
                quoted(folder)?
            ),
            256 * 1024,
        )
        .await?;
    static VALIDITY: OnceLock<Regex> = OnceLock::new();
    let pattern = VALIDITY.get_or_init(|| Regex::new(r"(?i)UIDVALIDITY\s+(\d+)").unwrap());
    rows.iter()
        .find_map(|r| pattern.captures(&r.line).map(|c| c[1].to_owned()))
        .ok_or_else(|| error("Сервер не подтвердил UIDVALIDITY папки"))
}
pub fn child(name: &str, parent: &str, folders: &[Value]) -> Result<String> {
    if name.trim().is_empty() || name.chars().count() > 200 || name.chars().any(char::is_control) {
        return Err(ApiError::invalid("Название папки: от 1 до 200 символов"));
    }
    let separator = if parent.is_empty() {
        folders
            .first()
            .and_then(|f| f["delimiter"].as_str())
            .unwrap_or("/")
    } else {
        folders
            .iter()
            .find(|f| f["id"] == parent)
            .and_then(|f| f["delimiter"].as_str())
            .ok_or_else(ApiError::missing)?
    };
    if !parent.is_empty() && separator.is_empty() {
        return Err(ApiError::invalid("Сервер не поддерживает вложенные папки"));
    }
    if !separator.is_empty() && name.contains(separator) {
        return Err(ApiError::invalid(
            "В названии папки не должно быть разделителя уровней",
        ));
    }
    Ok(if parent.is_empty() {
        encode(name.trim())
    } else {
        format!("{parent}{separator}{}", encode(name.trim()))
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn russian_folder_names_and_inbox_order_survive_roundtrip() {
        for name in ["Входящие", "Работа/Клиенты", "A & B", "Проекты 📦"]
        {
            assert_eq!(decode(&encode(name)), name)
        }
        let rows = vec![
            Part {
                line: "* LIST (\\Sent) \"/\" \"Sent\"\r\n".into(),
                literal: None,
            },
            Part {
                line: "* LIST () \"/\" \"inbox\"\r\n".into(),
                literal: None,
            },
        ];
        let folders = parse_list(&rows);
        assert_eq!(folders[0]["id"], "INBOX");
        assert_eq!(folders[1]["sent"], true);
        assert!(child("bad/name", "", &folders).is_err());
    }
}
