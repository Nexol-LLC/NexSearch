//! Read a private COPY of Chromium's old local storage, never open its live DB.
//! Export only validated appearance preferences; no mail, passwords or scripts.
use rusty_leveldb::{DB, LdbIterator, Options};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn chromium_text(bytes: &[u8]) -> Option<String> {
    match bytes.first()? {
        1 => Some(bytes[1..].iter().map(|b| char::from(*b)).collect()),
        0 => {
            if !(bytes.len() - 1).is_multiple_of(2) {
                return None;
            }
            String::from_utf16(
                &bytes[1..]
                    .as_chunks::<2>()
                    .0
                    .iter()
                    .map(|b| u16::from_le_bytes([b[0], b[1]]))
                    .collect::<Vec<_>>(),
            )
            .ok()
        }
        _ => None,
    }
}
fn normalize(value: Value) -> Option<Value> {
    let theme = value["theme"]
        .as_str()
        .filter(|v| matches!(*v, "dark" | "light"))?;
    let density = value["density"]
        .as_str()
        .filter(|v| matches!(*v, "compact" | "comfortable"))
        .unwrap_or("comfortable");
    Some(
        json!({"theme":theme,"density":density,"motion":value["motion"].as_bool().unwrap_or(true)}),
    )
}
pub fn read(home: &Path) -> BTreeMap<String, Value> {
    fn extract(home: &Path) -> Option<BTreeMap<String, Value>> {
        let source = home.join("desktop/Local Storage/leveldb");
        if !source.is_dir() {
            return Some(BTreeMap::new());
        }
        let temp = tempfile::Builder::new()
            .prefix("appearance-migration-")
            .tempdir_in(home)
            .ok()?;
        let snapshot = temp.path().join("leveldb");
        fs::create_dir(&snapshot).ok()?;
        let mut size = 0u64;
        let mut count = 0;
        for entry in fs::read_dir(source).ok()? {
            let entry = entry.ok()?;
            let kind = entry.file_type().ok()?;
            if !kind.is_file() {
                continue;
            }
            let name = entry.file_name();
            let name = name.to_str()?;
            if name == "LOCK"
                || !(name == "CURRENT"
                    || name.starts_with("MANIFEST-")
                    || name.ends_with(".ldb")
                    || name.ends_with(".sst")
                    || name.ends_with(".log"))
            {
                continue;
            }
            size = size.checked_add(entry.metadata().ok()?.len())?;
            count += 1;
            if size > 128 * 1024 * 1024 || count > 1000 {
                return None;
            }
            fs::copy(entry.path(), snapshot.join(name)).ok()?;
        }
        let mut db = DB::open(
            &snapshot,
            Options {
                create_if_missing: false,
                paranoid_checks: true,
                ..Options::default()
            },
        )
        .ok()?;
        let mut iter = db.new_iter().ok()?;
        let mut prefs = BTreeMap::new();
        let origin = fs::read(home.join("interface.json"))
            .ok()
            .and_then(|v| serde_json::from_slice::<Value>(&v).ok())
            .and_then(|v| {
                v["url"]
                    .as_str()
                    .map(|v| v.trim_end_matches('/').to_owned())
            })
            .filter(|s| s.starts_with("http://127.0.0.1:"))
            .unwrap_or_else(|| "http://127.0.0.1:8765".into());
        let prefix = format!("_{origin}\0").into_bytes();
        for _ in 0..10000 {
            let Some((key, bytes)) = iter.next() else {
                break;
            };
            let Some(key) = key.strip_prefix(prefix.as_slice()).and_then(chromium_text) else {
                continue;
            };
            let Some(owner) = key.strip_prefix("nexsearch.appearance.") else {
                continue;
            };
            if owner != "default" && owner != "last" && uuid::Uuid::parse_str(owner).is_err() {
                continue;
            }
            if bytes.len() > 8192 {
                continue;
            }
            if let Some(value) = chromium_text(&bytes)
                .and_then(|v| serde_json::from_str(&v).ok())
                .and_then(normalize)
            {
                prefs.insert(key, value);
            }
        }
        Some(prefs)
    }
    extract(home).unwrap_or_default()
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn imports_only_appearance_without_changing_the_original_chromium_database() {
        let home = tempfile::tempdir().unwrap();
        let folder = home.path().join("desktop/Local Storage/leveldb");
        fs::create_dir_all(&folder).unwrap();
        let owner = uuid::Uuid::new_v4();
        let key = format!("_http://127.0.0.1:8765\0\x01nexsearch.appearance.{owner}");
        {
            let mut db = DB::open(&folder, Options::default()).unwrap();
            db.put(
                key.as_bytes(),
                b"\x01{\"theme\":\"dark\",\"density\":\"compact\",\"motion\":false}",
            )
            .unwrap();
            db.put(
                b"_http://127.0.0.1:8765\0\x01token",
                b"private-do-not-export",
            )
            .unwrap();
            db.flush().unwrap();
        }
        let before = fs::read(folder.join("CURRENT")).unwrap();
        let prefs = read(home.path());
        assert_eq!(prefs.len(), 1);
        assert_eq!(
            prefs[&format!("nexsearch.appearance.{owner}")]["theme"],
            "dark"
        );
        assert_eq!(
            prefs[&format!("nexsearch.appearance.{owner}")]["motion"],
            false
        );
        assert_eq!(before, fs::read(folder.join("CURRENT")).unwrap());
        assert!(
            !serde_json::to_string(&prefs)
                .unwrap()
                .contains("private-do-not-export")
        );
    }
}
