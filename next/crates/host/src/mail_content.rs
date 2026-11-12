use crate::{
    error::{ApiError, Result},
    mail_transport::error,
};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use mail_parser::{HeaderValue, MessageParser, MimeHeaders};
use regex::Regex;
use serde_json::{Value, json};
use std::{
    borrow::Cow,
    collections::{HashMap, HashSet},
    sync::OnceLock,
};
pub fn message_id(value: &str) -> String {
    let value = value.trim().trim_matches(['<', '>']);
    if value.is_empty()
        || value.len() > 998
        || value
            .chars()
            .any(|c| c.is_whitespace() || c == '<' || c == '>')
    {
        String::new()
    } else {
        format!("<{value}>")
    }
}
fn ids(value: &HeaderValue<'_>) -> Vec<String> {
    let values: Vec<_> = if let Some(values) = value.as_text_list() {
        values.iter().map(|v| v.as_ref()).collect()
    } else {
        value.as_text().into_iter().collect()
    };
    let values: Vec<_> = values
        .into_iter()
        .map(message_id)
        .filter(|s| !s.is_empty())
        .collect();
    values[values.len().saturating_sub(50)..].to_vec()
}
pub fn header(raw: &[u8], metadata: &str) -> Result<Value> {
    let m = MessageParser::default()
        .parse(raw)
        .ok_or_else(|| error("Не удалось разобрать заголовки письма"))?;
    let sender = m
        .from()
        .and_then(|a| a.first())
        .map(|a| match (a.name.as_deref(), a.address.as_deref()) {
            (Some(name), Some(address)) => format!("{name} <{address}>"),
            (_, Some(address)) => address.to_owned(),
            _ => String::new(),
        })
        .unwrap_or_default();
    let addresses: Vec<_> = [m.from(), m.to(), m.cc()]
        .into_iter()
        .flatten()
        .flat_map(|a| a.iter())
        .filter_map(|a| a.address.as_deref().map(str::to_lowercase))
        .collect();
    let recipients: Vec<_> = [m.to(), m.cc()]
        .into_iter()
        .flatten()
        .flat_map(|a| a.iter())
        .filter_map(|a| a.address.as_deref().map(str::to_owned))
        .collect();
    static SIZE: OnceLock<Regex> = OnceLock::new();
    let size = SIZE
        .get_or_init(|| Regex::new(r"(?i)RFC822.SIZE\s+(\d+)").unwrap())
        .captures(metadata)
        .and_then(|c| c[1].parse::<u64>().ok())
        .unwrap_or(raw.len() as u64);
    Ok(
        json!({"subject":m.subject().unwrap_or("").chars().take(2000).collect::<String>(),"sender":sender,"recipients":recipients,"addresses":addresses,"sent_at":m.date().map(|d|d.to_rfc3339()),"seen":metadata.contains("\\Seen"),"size_bytes":size,"message_id":m.message_id().map(message_id).unwrap_or_default(),"in_reply_to":ids(m.in_reply_to()).first().cloned().unwrap_or_default(),"reference_ids":ids(m.references())}),
    )
}
pub fn raster(raw: &[u8]) -> Option<&'static str> {
    if raw.len() > 2 * 1024 * 1024 {
        return None;
    }
    if raw.starts_with(b"\x89PNG\r\n\x1a\n") {
        Some("image/png")
    } else if raw.starts_with(b"\xff\xd8\xff") {
        Some("image/jpeg")
    } else if raw.starts_with(b"GIF87a") || raw.starts_with(b"GIF89a") {
        Some("image/gif")
    } else if raw.starts_with(b"RIFF") && raw.get(8..12) == Some(b"WEBP") {
        Some("image/webp")
    } else {
        None
    }
}
fn embedded(value: &str, inline: &HashMap<String, String>) -> Option<String> {
    if let Some(id) = value.strip_prefix("cid:") {
        let id = percent_encoding::percent_decode_str(id)
            .decode_utf8()
            .ok()?;
        return inline.get(id.trim_matches(['<', '>'])).cloned();
    }
    if let Some(uri) = inline.get(value) {
        return Some(uri.clone());
    }
    if value.len() > 3 * 1024 * 1024 {
        return None;
    }
    let (kind, data) = value.strip_prefix("data:")?.split_once(";base64,")?;
    let bytes = STANDARD
        .decode(
            data.chars()
                .filter(|c| !c.is_whitespace())
                .collect::<String>(),
        )
        .ok()?;
    let actual = raster(&bytes)?;
    if actual != kind {
        return None;
    }
    Some(format!("data:{actual};base64,{}", STANDARD.encode(bytes)))
}
pub fn remote_url(value: &str) -> Option<String> {
    let value = if value.starts_with("//") {
        format!("https:{value}")
    } else {
        value.into()
    };
    let u = url::Url::parse(&value).ok()?;
    if !matches!(u.scheme(), "http" | "https")
        || u.host_str().is_none()
        || !u.username().is_empty()
        || u.password().is_some()
        || u.port()
            .is_some_and(|p| p != if u.scheme() == "https" { 443 } else { 80 })
        || value.len() > 3000
        || value
            .chars()
            .any(|c| c.is_control() || c.is_whitespace() || c == '\\')
    {
        return None;
    }
    Some(u.to_string())
}
fn clean_style(value: &str) -> String {
    let allowed = [
        "color",
        "background-color",
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "text-align",
        "text-decoration",
        "line-height",
        "letter-spacing",
        "white-space",
        "border",
        "border-color",
        "border-width",
        "border-style",
        "border-collapse",
        "border-spacing",
        "border-radius",
        "padding",
        "padding-top",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "margin",
        "margin-top",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "width",
        "min-width",
        "max-width",
        "height",
        "max-height",
        "vertical-align",
        "display",
        "list-style-type",
    ];
    value
        .split(';')
        .filter_map(|item| {
            let (k, v) = item.split_once(':')?;
            let k = k.trim().to_ascii_lowercase();
            let v = v.trim();
            let lower = v.to_ascii_lowercase();
            if !allowed.contains(&k.as_str())
                || v.len() > 180
                || v.chars()
                    .any(|c| !c.is_ascii_alphanumeric() && !"#%., ()-+\"'".contains(c))
                || ["url", "expression", "javascript", "var(", "attr("]
                    .iter()
                    .any(|s| lower.contains(s))
            {
                None
            } else {
                Some(format!("{k}:{v}"))
            }
        })
        .collect::<Vec<_>>()
        .join(";")
}
pub fn sanitize(html: &str, inline: HashMap<String, String>) -> String {
    let mut builder = ammonia::Builder::default();
    builder
        .url_schemes(
            ["https", "http", "mailto", "cid", "data"]
                .into_iter()
                .collect::<HashSet<_>>(),
        )
        .url_relative(ammonia::UrlRelative::Deny)
        .add_generic_attributes(&[
            "style", "align", "valign", "bgcolor", "color", "width", "height",
        ]);
    builder.attribute_filter(move |tag, attr, value| {
        if attr == "style" {
            return Some(Cow::Owned(clean_style(value)));
        }
        if attr == "src" && tag == "img" {
            return embedded(value, &inline)
                .or_else(|| remote_url(value))
                .map(Cow::Owned);
        }
        if attr == "href" {
            return if value.starts_with("mailto:") && !value.chars().any(char::is_control) {
                Some(Cow::Borrowed(value))
            } else {
                remote_url(value).map(Cow::Owned)
            };
        }
        Some(Cow::Borrowed(value))
    });
    let clean = builder
        .clean(&html.chars().take(1_000_000).collect::<String>())
        .to_string();
    static IMG: OnceLock<Regex> = OnceLock::new();
    static SRC: OnceLock<Regex> = OnceLock::new();
    let img = IMG.get_or_init(|| Regex::new(r"(?i)<img\b[^>]*>").unwrap());
    let src = SRC.get_or_init(|| Regex::new(r#"\bsrc="([^"]*)""#).unwrap());
    let mut index = 0;
    img.replace_all(&clean, |m: &regex::Captures<'_>| {
        let tag = &m[0];
        let Some(c) = src.captures(tag) else {
            return tag.to_owned();
        };
        let value = html_escape::decode_html_entities(&c[1]);
        if value.starts_with("data:") {
            return tag.to_owned();
        }
        let replacement = if let Some(url) = remote_url(&value) {
            if index >= 40 {
                String::new()
            } else {
                let result = format!(
                    "data-mail-remote=\"{}\" data-mail-image=\"{index}\"",
                    html_escape::encode_double_quoted_attribute(&url)
                );
                index += 1;
                result
            }
        } else {
            String::new()
        };
        src.replace(tag, replacement.as_str()).into_owned()
    })
    .into_owned()
}
pub fn image_urls(html: &str) -> Vec<String> {
    let document = scraper::Html::parse_fragment(html);
    let selector = scraper::Selector::parse("img[data-mail-image]").unwrap();
    document
        .select(&selector)
        .take(40)
        .map(|n| n.value().attr("data-mail-remote").unwrap_or("").to_owned())
        .collect()
}
pub fn rich(raw: &[u8]) -> Result<Value> {
    let m = MessageParser::default()
        .parse(raw)
        .ok_or_else(|| error("Не удалось разобрать письмо"))?;
    let text = m
        .body_text(0)
        .unwrap_or_default()
        .chars()
        .take(1_000_000)
        .collect::<String>();
    let html = m.body_html(0).unwrap_or_default();
    let mut inline = HashMap::new();
    let mut attachments = vec![];
    for (index, part) in m.parts.iter().enumerate() {
        let data = part.contents();
        if let Some(kind) = raster(data) {
            let uri = format!("data:{kind};base64,{}", STANDARD.encode(data));
            if let Some(id) = part.content_id() {
                inline.insert(id.trim_matches(['<', '>']).into(), uri.clone());
            }
            if let Some(location) = part.content_location() {
                inline.insert(location.into(), uri);
            }
        }
        if part.attachment_name().is_some()
            || part
                .content_disposition()
                .is_some_and(|d| d.c_type == "attachment")
        {
            attachments.push(json!({"index":index,"name":part.attachment_name().unwrap_or("Вложение").chars().take(300).collect::<String>(),"type":part.content_type().map(|t|format!("{}/{}",t.c_type,t.c_subtype.as_deref().unwrap_or("octet-stream"))).unwrap_or_else(||"application/octet-stream".into()),"size":data.len()}));
        }
    }
    let mut out = header(raw, "")?;
    out["body_text"] = text.into();
    out["body_html"] = sanitize(&html, inline).into();
    out["attachments"] = attachments.into();
    Ok(out)
}
pub fn attachment(raw: &[u8], index: usize, old_name: Option<&str>) -> Result<(String, Vec<u8>)> {
    let m = MessageParser::default()
        .parse(raw)
        .ok_or_else(|| error("Не удалось разобрать вложение"))?;
    let part = if let Some(name) = old_name {
        m.parts.iter().find(|p| p.attachment_name() == Some(name))
    } else {
        m.parts.get(index)
    }
    .ok_or_else(ApiError::missing)?;
    if part.attachment_name().is_none()
        && part
            .content_disposition()
            .is_none_or(|d| d.c_type != "attachment")
    {
        return Err(ApiError::missing());
    }
    Ok((
        part.attachment_name().unwrap_or("Вложение").to_owned(),
        part.contents().to_vec(),
    ))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn active_mail_and_external_images_are_always_isolated() {
        let raw = "<script>alert(1)</script><form>bad</form><img src='https://example.org/pixel.png'><img src='data:image/svg+xml;base64,PHN2Zz4='><a href='javascript:alert(1)'>bad</a><p style='color:red;background-image:url(https://bad);width:100%'>Письмо</p>";
        let clean = sanitize(raw, HashMap::new());
        assert!(!clean.contains("<script"));
        assert!(!clean.contains("javascript:"));
        assert!(!clean.contains("<form"));
        assert!(!clean.contains("svg+xml"));
        assert!(clean.contains("data-mail-image=\"0\""));
        assert_eq!(image_urls(&clean), vec!["https://example.org/pixel.png"]);
        assert!(clean.contains("color:red"));
        assert!(!clean.contains("background-image"));
    }
    #[test]
    fn message_references_keep_original_angle_brackets() {
        assert_eq!(message_id("abc@example.org"), "<abc@example.org>");
        assert_eq!(message_id("<abc@example.org>"), "<abc@example.org>");
        assert!(message_id("bad\r\nHeader").is_empty());
    }
}
