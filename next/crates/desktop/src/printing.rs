//! Isolated local print preview. It receives no application IPC permissions.
use std::io::Write;
use tauri::Manager;

fn document(html: &str) -> Result<String, String> {
    if html.len() > 16 * 1024 * 1024 || !html.starts_with("<!DOCTYPE html>") {
        return Err("Некорректная или слишком большая печатная форма".into());
    }
    let policy = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'none'; form-action 'none'; base-uri 'none'\">";
    if !html.contains("<head>") {
        return Err("В печатной форме отсутствует заголовок".into());
    }
    Ok(html.replacen(
        "<head>",
        &format!("<head>{policy}<style>#print-packing{{display:none}}</style>"),
        1,
    ))
}

#[tauri::command]
pub async fn export_pdf(app: tauri::AppHandle, html: String, name: String) -> Result<(), String> {
    let content = document(&html)?;
    let cache = app
        .path()
        .app_cache_dir()
        .map_err(|_| "Папка печати недоступна")?
        .join("print");
    std::fs::create_dir_all(&cache).map_err(|_| "Не удалось подготовить печатную форму")?;
    let id = uuid::Uuid::new_v4();
    let mut file = tempfile::Builder::new()
        .prefix("nexsearch-")
        .suffix(".html")
        .tempfile_in(&cache)
        .map_err(|_| "Не удалось сохранить печатную форму")?;
    file.write_all(content.as_bytes())
        .and_then(|_| file.as_file().sync_all())
        .map_err(|_| "Не удалось записать печатную форму")?;
    let path = file.into_temp_path();
    let url = tauri::Url::from_file_path(&path).map_err(|_| "Недоступный путь печати")?;
    let allowed = url.clone();
    let title: String = name.chars().filter(|c| !c.is_control()).take(100).collect();
    let window = tauri::WebviewWindowBuilder::new(
        &app,
        format!("print-{id}"),
        tauri::WebviewUrl::External(url),
    )
    .title(format!("{title} — печать / PDF"))
    .inner_size(1000., 780.)
    .on_navigation(move |destination| destination == &allowed)
    .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
    .on_page_load(|webview, payload| {
        if payload.event() == tauri::webview::PageLoadEvent::Finished {
            let _ = webview.print();
        }
    })
    .build()
    .map_err(|_| "Не удалось открыть окно печати")?;
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::Destroyed) {
            let _ = std::fs::remove_file(&path);
        }
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn print_document_blocks_scripts_network_and_forms_before_user_content() {
        let page = document(
            "<!DOCTYPE html><html><head><script>bad()</script></head><body>Груз</body></html>",
        )
        .unwrap();
        assert!(page.find("Content-Security-Policy").unwrap() < page.find("<script>").unwrap());
        for rule in [
            "default-src 'none'",
            "script-src 'none'",
            "form-action 'none'",
            "img-src data:",
        ] {
            assert!(page.contains(rule));
        }
        assert!(document("<script>bad()</script>").is_err());
        assert!(document("<!DOCTYPE html><body></body>").is_err());
    }
}
