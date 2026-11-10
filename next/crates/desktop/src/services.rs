use tauri::Manager;
#[derive(serde::Deserialize)]
pub struct Bounds {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}
impl Bounds {
    fn validate(&self) -> Result<(), String> {
        if [self.x, self.y, self.width, self.height]
            .iter()
            .any(|v| !v.is_finite() || *v < 0. || *v > 20_000.)
            || self.width < 1.
            || self.height < 1.
        {
            return Err("Недопустимый размер области сервиса".into());
        }
        Ok(())
    }
}
fn url(key: &str) -> Option<&'static str> {
    Some(match key {
        "calculator" => "https://tkblack.ru/calculator/",
        "signl" => "https://signl.ru/",
        "yandex" => "https://yandex.ru/maps",
        "2gis" => "https://2gis.ru/moscow/directions/tab/truck",
        "scales" => "https://nerudas.ru/vesy-platon",
        "cargo" => "https://routes.cargo.run/",
        "tracking" => "https://1track.ru/tracking",
        "clock" => "https://www.timeserver.ru/countries/ru",
        "pdf" => "https://www.ilovepdf.com/ru/split_pdf#split,extract",
        "optimus" => "https://optimus.mts.ru/",
        _ => return None,
    })
}
#[tauri::command]
pub async fn service_show(
    app: tauri::AppHandle,
    key: String,
    bounds: Bounds,
) -> Result<(), String> {
    let url = url(&key)
        .ok_or("Сервис не найден")?
        .parse()
        .map_err(|_| "Некорректная ссылка")?;
    show_url(app, url, bounds).await
}
pub(crate) async fn show_url(
    app: tauri::AppHandle,
    url: tauri::Url,
    bounds: Bounds,
) -> Result<(), String> {
    bounds.validate()?;
    if let Some(view) = app.get_webview("external-service") {
        view.navigate(url)
            .map_err(|_| "Не удалось открыть сервис")?;
        view.set_position(tauri::LogicalPosition::new(bounds.x, bounds.y))
            .map_err(|_| "Не удалось расположить сервис")?;
        view.set_size(tauri::LogicalSize::new(bounds.width, bounds.height))
            .map_err(|_| "Не удалось изменить размер сервиса")?;
        return view.show().map_err(|_| "Не удалось показать сервис".into());
    }
    let window = app.get_window("main").ok_or("Главное окно недоступно")?;
    let builder =
        tauri::webview::WebviewBuilder::new("external-service", tauri::WebviewUrl::External(url))
            .on_navigation(|url| matches!(url.scheme(), "https" | "http"))
            .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny);
    window
        .add_child(
            builder,
            tauri::LogicalPosition::new(bounds.x, bounds.y),
            tauri::LogicalSize::new(bounds.width, bounds.height),
        )
        .map_err(|_| "Не удалось создать область сервиса")?;
    Ok(())
}
#[tauri::command]
pub async fn service_hide(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(view) = app.get_webview("external-service") {
        view.hide().map_err(|_| "Не удалось скрыть сервис")?;
    }
    Ok(())
}
#[tauri::command]
pub async fn service_bounds(app: tauri::AppHandle, bounds: Bounds) -> Result<(), String> {
    if bounds.width < 1. || bounds.height < 1. {
        return service_hide(app).await;
    }
    bounds.validate()?;
    if let Some(view) = app.get_webview("external-service") {
        view.set_position(tauri::LogicalPosition::new(bounds.x, bounds.y))
            .map_err(|_| "Не удалось расположить сервис")?;
        view.set_size(tauri::LogicalSize::new(bounds.width, bounds.height))
            .map_err(|_| "Не удалось изменить размер сервиса")?;
    }
    Ok(())
}
#[tauri::command]
pub async fn service_reload(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(view) = app.get_webview("external-service") {
        view.reload().map_err(|_| "Не удалось обновить сервис")?;
    }
    Ok(())
}
#[tauri::command]
pub async fn service_back(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(view) = app.get_webview("external-service") {
        view.eval("history.back()")
            .map_err(|_| "Не удалось вернуться назад")?;
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn services_are_allowlisted_and_bounds_validated() {
        assert!(url("javascript:alert(1)").is_none());
        assert!(url("calculator").unwrap().starts_with("https://"));
        assert!(
            Bounds {
                x: f64::NAN,
                y: 0.,
                width: 500.,
                height: 300.
            }
            .validate()
            .is_err()
        );
        assert!(
            Bounds {
                x: 240.,
                y: 100.,
                width: 900.,
                height: 600.
            }
            .validate()
            .is_ok()
        );
    }
}
