#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use nexsearch_compute::{CpuInfo, Engine};
use nexsearch_core::{company::*, mailbox::Message, preferences::*, recommendation::*};
use nexsearch_jobs::{Cancellation, Coordinator, JobError};
use serde::Serialize;
use std::io::Write;
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
use tauri::{Manager, State};
use uuid::Uuid;

struct Preview {
    companies: Vec<Company>,
    employees: Vec<Employee>,
    preferences: Preferences,
    tasks: Vec<String>,
}
struct AppState {
    preview: Mutex<Preview>,
    engine: Arc<Engine>,
    mail: Coordinator,
}
#[derive(Serialize)]
struct Bootstrap {
    version: &'static str,
    preview: bool,
    employee: Employee,
    employees: Vec<Employee>,
    companies: Vec<Company>,
    preferences: Preferences,
    tasks: Vec<String>,
    messages: Vec<Message>,
    cpu: CpuInfo,
}
#[tauri::command]
fn bootstrap(state: State<AppState>) -> Result<Bootstrap, String> {
    let s = state.preview.lock().map_err(|_| "Состояние недоступно")?;
    let employee = s.employees[0].clone();
    let companies = s
        .companies
        .iter()
        .filter(|c| {
            c.owner_id.is_none_or(|id| {
                s.employees
                    .iter()
                    .any(|e| e.id == id && employee.can_view_clients_of(e))
            })
        })
        .cloned()
        .collect();
    Ok(Bootstrap {
        version: env!("CARGO_PKG_VERSION"),
        preview: true,
        employee,
        employees: s.employees.clone(),
        companies,
        preferences: s.preferences.clone(),
        tasks: s.tasks.clone(),
        messages: messages(),
        cpu: state.engine.info.clone(),
    })
}
#[tauri::command]
fn save_preferences(
    app: tauri::AppHandle,
    state: State<AppState>,
    preferences: Preferences,
) -> Result<(), String> {
    let mut s = state.preview.lock().map_err(|_| "Состояние недоступно")?;
    if preferences.employee_id != s.employees[0].id {
        return Err("Настройки другого сотрудника недоступны".into());
    }
    let services = preferences.services.clone();
    let mut valid = Preferences {
        services: vec![],
        ..preferences
    };
    for service in services {
        valid.add_service(service).map_err(String::from)?;
    }
    if valid.proactive_search {
        return Err("Автоматический поиск ещё не подключён".into());
    }
    let dir = app
        .path()
        .app_config_dir()
        .map_err(|_| "Папка настроек недоступна")?;
    std::fs::create_dir_all(&dir).map_err(|_| "Не удалось создать папку настроек")?;
    let bytes = serde_json::to_vec_pretty(&valid).map_err(|_| "Не удалось сохранить настройки")?;
    let mut temp =
        tempfile::NamedTempFile::new_in(&dir).map_err(|_| "Не удалось записать настройки")?;
    temp.write_all(&bytes)
        .and_then(|_| temp.as_file().sync_all())
        .map_err(|_| "Не удалось записать настройки")?;
    temp.persist(dir.join("preferences.json"))
        .map_err(|_| "Не удалось сохранить настройки")?;
    s.preferences = valid;
    Ok(())
}

#[tauri::command]
fn search_preview(
    state: State<AppState>,
    criteria: SearchCriteria,
) -> Result<Vec<Company>, String> {
    criteria.validate().map_err(String::from)?;
    let s = state.preview.lock().map_err(|_| "Состояние недоступно")?;
    Ok(s.companies
        .iter()
        .filter(|c| criteria.matches(c))
        .take(criteria.count as usize)
        .cloned()
        .collect())
}
#[derive(Serialize)]
struct Suggested {
    company: Company,
    employee: Employee,
    score: f64,
    reasons: Vec<String>,
}
#[tauri::command]
async fn recommend_preview(state: State<'_, AppState>) -> Result<Vec<Suggested>, String> {
    let (companies, employees) = {
        let s = state.preview.lock().map_err(|_| "Состояние недоступно")?;
        (s.companies.clone(), s.employees.clone())
    };
    let engine = state.engine.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut metadata = vec![];
        let mut rows = vec![];
        for company in companies {
            for employee in employees.iter().filter(|e| e.role == Role::Logistician) {
                let profile = WorkProfile {
                    industries: vec![
                        if employee.id == Uuid::from_u128(2) {
                            "Техника"
                        } else {
                            "Продукты"
                        }
                        .into(),
                    ],
                    regions: vec!["Москва".into()],
                    routes: vec!["Москва — Казань".into()],
                    available_capacity: 10,
                };
                if let Some(r) = recommend(&company, employee, &profile) {
                    let features = vec![
                        f64::from(profile.industries.contains(&company.industry)),
                        f64::from(profile.regions.contains(&company.region)),
                        f64::from(company.routes.iter().any(|r| profile.routes.contains(r))),
                    ];
                    rows.push(features);
                    metadata.push((company.clone(), employee.clone(), r.reasons));
                }
            }
        }
        let scores = engine
            .score(&[40., 25., 30.], &rows)
            .map_err(|_| "Не удалось рассчитать рекомендации".to_owned())?;
        let mut output: Vec<_> = metadata
            .into_iter()
            .zip(scores)
            .map(|((company, employee, reasons), score)| Suggested {
                company,
                employee,
                score,
                reasons,
            })
            .collect();
        output.sort_by(|a, b| b.score.total_cmp(&a.score));
        Ok(output)
    })
    .await
    .map_err(|_| "Расчёт прерван".to_owned())?
}
#[tauri::command]
fn claim_preview(state: State<AppState>, company_id: Uuid) -> Result<(), String> {
    let mut s = state.preview.lock().map_err(|_| "Состояние недоступно")?;
    let owner = s.employees[0].id;
    let company = s
        .companies
        .iter_mut()
        .find(|c| c.id == company_id)
        .ok_or("Компания недоступна")?;
    if company.owner_id.is_some() {
        return Err("Компания уже закреплена за сотрудником".into());
    }
    if !company.verified_active() {
        return Err("Действующий статус компании не подтверждён".into());
    }
    company.owner_id = Some(owner);
    company.stage = "Первый контакт".into();
    Ok(())
}
#[tauri::command]
fn add_task_preview(state: State<AppState>, company_id: Uuid) -> Result<(), String> {
    let mut s = state.preview.lock().map_err(|_| "Состояние недоступно")?;
    let company = s
        .companies
        .iter()
        .find(|c| c.id == company_id)
        .ok_or("Компания недоступна")?;
    let text = format!("Связаться: {}", company.name);
    s.tasks.push(text);
    Ok(())
}
#[tauri::command]
fn transfer_preview(
    state: State<AppState>,
    company_id: Uuid,
    employee_id: Uuid,
) -> Result<(), String> {
    let mut s = state.preview.lock().map_err(|_| "Состояние недоступно")?;
    let target = s
        .employees
        .iter()
        .find(|e| e.id == employee_id)
        .ok_or("Сотрудник недоступен")?;
    if !s.employees[0].can_assign_to(target) {
        return Err("Передача недоступна вашей должности".into());
    }
    let company = s
        .companies
        .iter_mut()
        .find(|c| c.id == company_id)
        .ok_or("Компания недоступна")?;
    company.owner_id = Some(employee_id);
    company.stage = "Первый контакт".into();
    Ok(())
}
#[tauri::command]
fn open_service(state: State<AppState>, service_id: Uuid) -> Result<(), String> {
    let url = {
        let s = state.preview.lock().map_err(|_| "Состояние недоступно")?;
        s.preferences
            .services
            .iter()
            .find(|s| s.id == service_id)
            .ok_or("Сервис недоступен")?
            .url
            .clone()
    };
    open::that(url.as_str()).map_err(|_| "Не удалось открыть сервис".to_owned())
}
#[tauri::command]
async fn sync_preview(state: State<'_, AppState>) -> Result<&'static str, String> {
    match state
        .mail
        .run(
            Uuid::nil(),
            Duration::from_secs(2),
            Cancellation::default(),
            async {
                tokio_sleep().await;
                Ok(())
            },
        )
        .await
    {
        Ok(()) => Ok("Учебная проверка завершена. Настоящий ящик пока не подключён."),
        Err(JobError::AlreadyRunning) => Ok("Проверка уже идёт, результат появится автоматически."),
        _ => Err("Проверка не завершена. Можно повторить.".into()),
    }
}
async fn tokio_sleep() {
    tauri::async_runtime::spawn_blocking(|| std::thread::sleep(Duration::from_millis(400)))
        .await
        .ok();
}

fn employees() -> Vec<Employee> {
    [
        (1, "Руководитель · учебный профиль", Role::Manager),
        (2, "Анна · учебный профиль", Role::Logistician),
        (3, "Михаил · учебный профиль", Role::Logistician),
    ]
    .into_iter()
    .map(|(i, name, role)| Employee {
        id: Uuid::from_u128(i),
        name: name.into(),
        role,
        department_id: Uuid::nil(),
    })
    .collect()
}
fn companies() -> Vec<Company> {
    (0..6)
        .map(|i| {
            let digits = format!("77001234{i}");
            let check = digits
                .bytes()
                .zip([2, 4, 10, 3, 5, 9, 4, 6, 8])
                .map(|(b, w)| (b - b'0') as u32 * w)
                .sum::<u32>()
                % 11
                % 10;
            Company {
                id: Uuid::from_u128(100 + i),
                name: format!(
                    "Пример · {}",
                    [
                        "Технопоставка",
                        "Северные продукты",
                        "Стройресурс",
                        "Технолиния",
                        "Регионснаб",
                        "Транспортные системы"
                    ][i as usize]
                ),
                inn: Inn::try_from(format!("{digits}{check}")).unwrap(),
                active: Some(true),
                evidence: Some(Evidence {
                    source: "Учебные данные, не сведения о реальной компании".into(),
                    checked_at: "2026-11-10".into(),
                }),
                industry: if i % 2 == 0 {
                    "Техника"
                } else {
                    "Продукты"
                }
                .into(),
                region: "Москва".into(),
                timezone: "Europe/Moscow · МСК".into(),
                revenue_rub: Some(50_000_000 * (i as u64 + 1)),
                revenue_year: Some(2025),
                phone: "Не указан".into(),
                email: "Не указана".into(),
                routes: vec!["Москва — Казань".into()],
                owner_id: if i < 2 {
                    Some(Uuid::from_u128(i + 2))
                } else {
                    None
                },
                stage: if i < 2 {
                    "В работе"
                } else {
                    "Новая компания"
                }
                .into(),
            }
        })
        .collect()
}
fn messages() -> Vec<Message> {
    vec![
        Message {
            id: "1".into(),
            message_id: "a".into(),
            in_reply_to: None,
            references: vec![],
            subject: "Пример: перевозка Москва — Казань".into(),
            sender: "Клиент · пример".into(),
            body: "Добрый день! Можно уточнить условия перевозки? Это учебное письмо.".into(),
            sent_at: 1,
            seen: false,
        },
        Message {
            id: "2".into(),
            message_id: "b".into(),
            in_reply_to: Some("a".into()),
            references: vec!["a".into()],
            subject: "Ответ: перевозка Москва — Казань".into(),
            sender: "Вы · учебный ответ".into(),
            body: "Добрый день! Уточните, пожалуйста, массу и размеры груза.".into(),
            sent_at: 2,
            seen: true,
        },
    ]
}
fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let employee_id = Uuid::from_u128(1);
            let preferences = app
                .path()
                .app_config_dir()
                .ok()
                .and_then(|p| std::fs::read(p.join("preferences.json")).ok())
                .filter(|b| b.len() <= 1024 * 1024)
                .and_then(|b| serde_json::from_slice::<Preferences>(&b).ok())
                .filter(|p| p.employee_id == employee_id)
                .unwrap_or(Preferences {
                    employee_id,
                    ..Default::default()
                });
            app.manage(AppState {
                preview: Mutex::new(Preview {
                    companies: companies(),
                    employees: employees(),
                    preferences,
                    tasks: vec![],
                }),
                engine: Arc::new(
                    Engine::new(None).map_err(|_| "Не удалось запустить вычислительный модуль")?,
                ),
                mail: Coordinator::new(3).map_err(std::io::Error::other)?,
            });
            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("NexSearch 2.0 · предварительная версия")
            .inner_size(1380., 920.)
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
            bootstrap,
            save_preferences,
            search_preview,
            recommend_preview,
            claim_preview,
            add_task_preview,
            transfer_preview,
            open_service,
            sync_preview
        ])
        .run(tauri::generate_context!())
        .expect("Не удалось открыть NexSearch 2.0");
}
