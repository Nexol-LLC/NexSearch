use serde_json::{Value, json};
use std::sync::OnceLock;
fn zones() -> &'static Value {
    static DATA: OnceLock<Value> = OnceLock::new();
    DATA.get_or_init(|| {
        serde_json::from_str(include_str!("../../../data/timezones.json")).expect("Часовые пояса")
    })
}
fn text(v: &Value, key: &str) -> String {
    v[key]
        .as_str()
        .unwrap_or("")
        .to_lowercase()
        .replace('ё', "е")
}
fn contains_word(haystack: &str, needle: &str) -> bool {
    haystack.match_indices(needle).any(|(i, _)| {
        haystack[..i]
            .chars()
            .next_back()
            .is_none_or(|c| !c.is_alphanumeric())
            && haystack[i + needle.len()..]
                .chars()
                .next()
                .is_none_or(|c| !c.is_alphanumeric())
    })
}
pub fn offset(company: &Value) -> Option<i32> {
    let region = text(company, "region");
    let mut names = vec![];
    for (hours, values) in zones()["regions"].as_object()? {
        for name in values.as_array()? {
            names.push((
                name.as_str()?.to_lowercase().replace('ё', "е"),
                hours.parse::<i32>().ok()?,
            ))
        }
    }
    names.sort_by_key(|(name, _)| std::cmp::Reverse(name.len()));
    for (name, hours) in names {
        if region.contains(&name) {
            return Some(hours);
        }
    }
    if ["москва", "г.москва", "г. москва", "г москва"].contains(&region.as_str())
    {
        return Some(3);
    }
    let address = format!(
        "{} {} {}",
        text(company, "city"),
        text(company, "legal_address"),
        text(company, "address")
    );
    let mut cities: Vec<_> = zones()["cities"].as_object()?.iter().collect();
    cities.sort_by_key(|(name, _)| std::cmp::Reverse(name.len()));
    for (name, hours) in cities {
        if contains_word(&address, &name.to_lowercase().replace('ё', "е")) {
            return hours.as_i64().map(|h| h as i32);
        }
    }
    None
}
pub fn display(company: &Value) -> Value {
    if let Some(h) = offset(company) {
        let difference = h - 3;
        let label = if difference == 0 {
            format!("UTC+{h} · МСК")
        } else {
            format!("UTC+{h} · МСК{difference:+}")
        };
        let zone = chrono::FixedOffset::east_opt(h * 3600);
        return json!({"utc_offset":h,"label":label,"local_time":zone.map(|z|chrono::Utc::now().with_timezone(&z).to_rfc3339())});
    }
    json!({"utc_offset":null,"label":"Часовой пояс не определён","local_time":null})
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn registration_region_and_ambiguous_city_match_legacy_rules() {
        assert_eq!(offset(&json!({"region":"Москва"})), Some(3));
        assert_eq!(
            offset(&json!({"region":"Калининградская область"})),
            Some(2)
        );
        assert_eq!(offset(&json!({"region":"Якутия"})), None);
        assert_eq!(
            offset(&json!({"region":"Якутия","legal_address":"г. Якутск, улица Ленина"})),
            Some(9)
        );
        assert_eq!(offset(&json!({"region":"Неизвестно"})), None);
    }
}
