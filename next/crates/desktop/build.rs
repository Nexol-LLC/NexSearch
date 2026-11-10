fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "legacy_request",
            "open_external",
            "save_file",
            "quit_app",
            "system_info",
            "copy_text",
            "service_show",
            "service_hide",
            "service_bounds",
            "service_reload",
            "service_back",
            "workspace_get",
            "workspace_save",
            "workspace_recommendations",
            "custom_service_show",
        ]),
    ))
    .expect("Не удалось подготовить настольное приложение");
}
