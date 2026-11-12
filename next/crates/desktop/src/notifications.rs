use base64::{Engine as _, engine::general_purpose::STANDARD};
use nexsearch_bridge::Bridge;
use serde::Deserialize;
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};
use tauri::{Emitter, Manager, State};
use tauri_plugin_notification::NotificationExt;
use uuid::Uuid;

#[derive(Default)]
pub struct Notifications(pub Mutex<Entries>);
#[derive(Default)]
pub struct Entries {
    epoch: u64,
    next: i32,
    items: BTreeMap<i32, (Uuid, Uuid)>,
}
#[derive(Deserialize)]
pub struct Popup {
    id: Uuid,
    owner: Uuid,
    title: String,
    body: String,
}
pub fn bind(app: &tauri::AppHandle) -> Result<(), String> {
    let app = app.clone();
    let callback = app.clone();
    app.notification()
        .on_action(move |event| {
            let Some(id) = serde_json::to_value(event)
                .ok()
                .and_then(|v| v["notification"]["id"].as_i64())
                .and_then(|v| i32::try_from(v).ok())
            else {
                return;
            };
            let state = callback.state::<Notifications>();
            let items = state.0.lock().unwrap_or_else(|e| e.into_inner());
            let Some((id, owner)) = items.items.get(&id) else {
                return;
            };
            let value = serde_json::json!({"id":id,"owner":owner});
            drop(items);
            if let Some(window) = callback.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
            let _ = callback.emit_to("main", "nexsearch:notification", value);
        })
        .map_err(|_| "Не удалось подключить системные уведомления".into())
}
#[tauri::command]
pub async fn notify_employee(
    app: tauri::AppHandle,
    bridge: State<'_, Arc<Bridge>>,
    state: State<'_, Notifications>,
    value: Popup,
) -> Result<bool, String> {
    if value.title.chars().count() > 300 || value.body.chars().count() > 2000 {
        return Err("Слишком длинное уведомление".into());
    }
    if let Some(window) = app.get_webview_window("main")
        && window.is_focused().unwrap_or(false)
        && !window.is_minimized().unwrap_or(false)
    {
        return Ok(false);
    }
    let epoch = state.0.lock().unwrap_or_else(|e| e.into_inner()).epoch;
    let reply = bridge.request("/api/me", "GET", None).await;
    let me: serde_json::Value = serde_json::from_slice(
        &STANDARD
            .decode(reply.body)
            .map_err(|_| "Не выполнен вход")?,
    )
    .map_err(|_| "Не выполнен вход")?;
    if reply.status != 200 || me["employee"]["id"].as_str() != Some(&value.owner.to_string()) {
        return Ok(false);
    }
    let id = {
        let mut entries = state.0.lock().unwrap_or_else(|e| e.into_inner());
        if entries.epoch != epoch || entries.items.values().any(|(id, _)| *id == value.id) {
            return Ok(false);
        }
        entries.next = entries.next.checked_add(1).unwrap_or(1);
        let id = entries.next;
        entries.items.insert(id, (value.id, value.owner));
        while entries.items.len() > 20 {
            if let Some(old) = entries.items.keys().next().copied() {
                entries.items.remove(&old);
            }
        }
        id
    };
    // Desktop backends cannot reliably retract delivered OS notifications on logout.
    // Keep private task/mail text inside the authenticated app; OS notifications carry no dossier data.
    app.notification()
        .builder()
        .id(id)
        .title("NexSearch")
        .body("Новое уведомление. Откройте программу, чтобы посмотреть подробности.")
        .silent()
        .show()
        .map_err(|_| "Системное уведомление недоступно")?;
    Ok(true)
}
#[tauri::command]
pub fn notifications_clear(
    app: tauri::AppHandle,
    state: State<'_, Notifications>,
) -> Result<(), String> {
    let mut entries = state.0.lock().unwrap_or_else(|e| e.into_inner());
    entries.epoch = entries.epoch.wrapping_add(1);
    entries.items.clear();
    drop(entries);
    app.notification()
        .remove_all_active()
        .map_err(|_| "Не удалось убрать системные уведомления".into())
}
