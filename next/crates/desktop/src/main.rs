#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use nexsearch_bridge::{Bridge, Reply, Trust};
use nexsearch_core::preferences::PersonalService;
use std::{io::Write, sync::Arc};
use tauri::{Manager, State};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_dialog::DialogExt;
mod services;
use services::*;
mod printing;
use printing::export_pdf;
mod workspace;
use workspace::*;
#[derive(serde::Deserialize)]
struct RuntimeConnection {
    endpoint: String,
    trust: Trust,
    import_session: Option<std::path::PathBuf>,
}
struct AppInstance {
    _file: std::fs::File,
}

#[tauri::command]
async fn legacy_request(
    state: State<'_, Arc<Bridge>>,
    updates: State<'_, Arc<nexsearch_updates::client::Client>>,
    path: String,
    method: String,
    body: Option<String>,
) -> Result<Reply, String> {
    if path == "/api/client/update/status" && method == "GET" {
        return Ok(Reply::json(200, updates.status()));
    }
    if path == "/api/client/update/check" && method == "POST" {
        return Ok(Reply::json(
            200,
            serde_json::json!({"queued":updates.start(true)}),
        ));
    }
    if path == "/api/client/update/install" && method == "POST" {
        updates.request_install()?;
        return Ok(Reply::json(
            200,
            serde_json::json!({"restart_scheduled":true}),
        ));
    }
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
        let mut dialog = app
            .dialog()
            .file()
            .set_file_name(&name)
            .set_title("Сохранить файл");
        if let Some(window) = app.get_window("main") {
            dialog = dialog.set_parent(&window);
        }
        let Some(file) = dialog.blocking_save_file() else {
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
            let program = std::env::current_exe()?
                .parent()
                .ok_or("Нет папки программы")?
                .to_owned();
            let config = std::env::var_os("NEXSEARCH_NEXT_CONFIG")
                .map(std::path::PathBuf::from)
                .and_then(|p| std::fs::read(p).ok())
                .and_then(|b| serde_json::from_slice::<RuntimeConnection>(&b).ok());
            let trust: Trust = config.as_ref().map(|c| c.trust.clone()).unwrap_or(
                std::fs::read(program.join("client-trust.json"))
                    .ok()
                    .and_then(|v| serde_json::from_slice(&v).ok())
                    .unwrap_or(serde_json::from_str(include_str!(
                        "../../../../network/client-trust.json"
                    ))?),
            );
            let bridge = Arc::new(Bridge::new(trust).map_err(std::io::Error::other)?);
            let store = nexsearch_updates::client::native_store(&bridge)
                .ok_or("Недоступна папка сеанса")?;
            tauri::async_runtime::block_on(async {
                let _ = bridge.configure_store(store).await;
                if config.is_none() {
                    bridge.prepare_legacy_session().await;
                }
                if let Some(config) = &config
                    && bridge.connect(&config.endpoint).await.is_ok()
                    && !bridge.has_session().await
                    && let Some(path) = &config.import_session
                {
                    let _ = bridge.import_session(path).await;
                }
            });
            app.manage(bridge.clone());
            let home = nexsearch_updates::client::employee_home(bridge.host_id())
                .unwrap_or(app.path().app_data_dir()?.join("employee"));
            std::fs::create_dir_all(&home)?;
            let instance = std::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .open(home.join("instance.lock"))?;
            instance
                .try_lock()
                .map_err(|_| std::io::Error::other("Программа сотрудника уже запущена"))?;
            app.manage(AppInstance { _file: instance });
            let updater = nexsearch_updates::client::Client::new(program, home, bridge.clone());
            app.manage(updater.clone());
            tauri::async_runtime::spawn(async move {
                loop {
                    tokio::time::sleep(std::time::Duration::from_secs(30)).await;
                    updater.start(false);
                }
            });
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
            .disable_drag_drop_handler()
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
            export_pdf,
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
        .build(tauri::generate_context!())
        .expect("Не удалось открыть NexSearch")
        .run(|app, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                let updater = app.state::<Arc<nexsearch_updates::client::Client>>();
                if let Err(error) = updater.launch_pending() {
                    eprintln!("Установка обновления не началась: {error}");
                }
            }
        });
}
