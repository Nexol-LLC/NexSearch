#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use nexsearch_bridge::{Bridge, Reply, Trust};
use nexsearch_core::preferences::PersonalService;
use std::{io::Write, sync::Arc};
use tauri::{Manager, State};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_dialog::DialogExt;
mod services;
use services::*;
mod workspace;
use workspace::*;
#[derive(serde::Deserialize)]
struct RuntimeConnection {
    endpoint: String,
    trust: Trust,
    import_session: Option<std::path::PathBuf>,
}

#[tauri::command]
async fn legacy_request(
    state: State<'_, Arc<Bridge>>,
    path: String,
    method: String,
    body: Option<String>,
) -> Result<Reply, String> {
    Ok(state.request(&path, &method, body).await)
}
#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    let link = PersonalService::new("Внешняя ссылка", &url).map_err(String::from)?;
    open::that(link.url.as_str()).map_err(|_| "Не удалось открыть ссылку".into())
}
#[tauri::command]
async fn save_file(app: tauri::AppHandle, bytes: Vec<u8>, name: String) -> Result<bool, String> {
    if bytes.len() > 50 * 1024 * 1024 {
        return Err("Файл больше допустимых 50 МБ".into());
    }
    let name = name
        .rsplit(['\\', '/'])
        .next()
        .unwrap_or("NexSearch.xlsx")
        .chars()
        .filter(|c| !c.is_control())
        .take(120)
        .collect::<String>();
    tauri::async_runtime::spawn_blocking(move || {
        let Some(file) = app
            .dialog()
            .file()
            .set_file_name(&name)
            .blocking_save_file()
        else {
            return Ok(false);
        };
        let path = file
            .into_path()
            .map_err(|_| "Недоступный путь сохранения".to_owned())?;
        let parent = path.parent().ok_or("Недоступная папка сохранения")?;
        let mut temp =
            tempfile::NamedTempFile::new_in(parent).map_err(|_| "Не удалось подготовить файл")?;
        temp.write_all(&bytes)
            .and_then(|_| temp.as_file().sync_all())
            .map_err(|_| "Не удалось записать файл")?;
        temp.persist(path)
            .map_err(|_| "Не удалось сохранить файл. Проверьте, не открыт ли он в Excel.")?;
        Ok(true)
    })
    .await
    .map_err(|_| "Сохранение прервано".to_owned())?
}
#[tauri::command]
fn quit_app(app: tauri::AppHandle) {
    app.exit(0)
}
#[tauri::command]
fn copy_text(app: tauri::AppHandle, text: String) -> Result<(), String> {
    if text.len() > 1024 * 1024 {
        return Err("Слишком большой текст для копирования".into());
    }
    app.clipboard()
        .write_text(text)
        .map_err(|_| "Не удалось скопировать текст".into())
}
#[tauri::command]
fn system_info() -> serde_json::Value {
    serde_json::json!({"version":env!("CARGO_PKG_VERSION"),"interface_baseline":"1.6.7","cpu_kernel":nexsearch_compute::Kernel::detect(),"migration_complete":false})
}
fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .setup(|app| {
            let config = std::env::var_os("NEXSEARCH_NEXT_CONFIG")
                .map(std::path::PathBuf::from)
                .and_then(|p| std::fs::read(p).ok())
                .and_then(|b| serde_json::from_slice::<RuntimeConnection>(&b).ok());
            let trust: Trust =
                config
                    .as_ref()
                    .map(|c| c.trust.clone())
                    .unwrap_or(serde_json::from_str(include_str!(
                        "../../../../network/client-trust.json"
                    ))?);
            let bridge = Arc::new(Bridge::new(trust).map_err(std::io::Error::other)?);
            let store = app
                .path()
                .app_config_dir()?
                .join(bridge.storage_scope())
                .join("session.sec");
            tauri::async_runtime::block_on(async {
                let _ = bridge.configure_store(store).await;
                if let Some(config) = &config
                    && bridge.connect(&config.endpoint).await.is_ok()
                    && let Some(path) = &config.import_session
                {
                    let _ = bridge.import_session(path).await;
                }
            });
            app.manage(bridge.clone());
            // Offline is used by UI verification. No office/VPS probe is needed to inspect the preserved interface.
            if std::env::var("NEXSEARCH_NEXT_OFFLINE").as_deref() != Ok("1") {
                tauri::async_runtime::spawn(bridge.discover());
            }
            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("NexSearch 2.0")
            .inner_size(1280., 850.)
            .min_inner_size(1000., 680.)
            .on_navigation(|url| {
                (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
                    || (matches!(url.scheme(), "http" | "https")
                        && url.host_str() == Some("tauri.localhost"))
            })
            .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
            .build()?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            legacy_request,
            open_external,
            save_file,
            quit_app,
            system_info,
            copy_text,
            service_show,
            service_hide,
            service_bounds,
            service_reload,
            service_back,
            workspace_get,
            workspace_save,
            workspace_recommendations,
            custom_service_show
        ])
        .run(tauri::generate_context!())
        .expect("Не удалось открыть NexSearch");
}
