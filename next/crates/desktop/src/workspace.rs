use base64::{Engine as _, engine::general_purpose::STANDARD};
use nexsearch_bridge::Bridge;
use nexsearch_compute::{
    Engine,
    learning::{Model, Sample},
};
use nexsearch_core::{preferences::Preferences, recommendation::WorkProfile};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, HashSet},
    io::Write,
    path::Path,
};
use tauri::{Manager, State};
use uuid::Uuid;

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct PersonalWorkspace {
    pub preferences: Preferences,
    pub profile: WorkProfile,
}
impl Default for PersonalWorkspace {
    fn default() -> Self {
        Self {
            preferences: Preferences::default(),
            profile: WorkProfile {
                available_capacity: 30,
                ..Default::default()
            },
        }
    }
}
async fn get(bridge: &Bridge, path: &str) -> Result<Value, String> {
    let reply = bridge.request(path, "GET", None).await;
    let bytes = STANDARD
        .decode(reply.body)
        .map_err(|_| "Некорректный ответ хоста")?;
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| "Некорректный ответ хоста")?;
    if reply.status != 200 {
        return Err(value["error"]
            .as_str()
            .unwrap_or("Данные недоступны")
            .into());
    }
    Ok(value)
}
fn id_of(me: &Value) -> Result<Uuid, String> {
    Uuid::parse_str(me["id"].as_str().ok_or("Не выполнен вход")?)
        .map_err(|_| "Некорректная учётная запись".into())
}
fn load(path: &Path, owner: Uuid) -> Result<PersonalWorkspace, String> {
    let mut saved = if path.exists() {
        let bytes = std::fs::read(path).map_err(|_| "Не удалось прочитать настройки")?;
        if bytes.len() > 1024 * 1024 {
            return Err("Настройки слишком велики".into());
        }
        serde_json::from_slice(&bytes).map_err(|_| "Повреждены личные настройки")?
    } else {
        PersonalWorkspace::default()
    };
    if saved.preferences.employee_id != Uuid::nil() && saved.preferences.employee_id != owner {
        return Err("Настройки относятся к другому сотруднику".into());
    }
    saved.preferences.employee_id = owner;
    Ok(saved)
}
fn path(app: &tauri::AppHandle, bridge: &Bridge, id: Uuid) -> Result<std::path::PathBuf, String> {
    Ok(app
        .path()
        .app_config_dir()
        .map_err(|_| "Папка настроек недоступна")?
        .join(bridge.storage_scope())
        .join(format!("personal-{id}.json")))
}
fn validate(p: &PersonalWorkspace, owner: Uuid) -> Result<(), String> {
    if p.preferences.employee_id != owner {
        return Err("Нельзя менять настройки другого сотрудника".into());
    }
    if p.profile.available_capacity > 1000 {
        return Err("Нагрузка должна быть от 0 до 1000 клиентов".into());
    }
    for values in [&p.profile.industries, &p.profile.regions, &p.profile.routes] {
        if values.len() > 100
            || values
                .iter()
                .any(|v| v.is_empty() || v.len() > 800 || v.chars().any(char::is_control))
        {
            return Err("Проверьте рабочий профиль: до 100 записей в каждой группе".into());
        }
    }
    let mut prefs = Preferences {
        services: vec![],
        ..p.preferences.clone()
    };
    for service in &p.preferences.services {
        prefs.add_service(service.clone()).map_err(String::from)?;
    }
    Ok(())
}
#[tauri::command]
pub async fn custom_service_show(
    app: tauri::AppHandle,
    bridge: State<'_, std::sync::Arc<Bridge>>,
    id: Uuid,
    bounds: crate::services::Bounds,
) -> Result<(), String> {
    let me = get(&bridge, "/api/me").await?["employee"].clone();
    let owner = id_of(&me)?;
    let saved = load(&path(&app, &bridge, owner)?, owner)?;
    let service = saved
        .preferences
        .services
        .iter()
        .find(|s| s.id == id)
        .ok_or("Личный сервис не найден")?;
    crate::services::show_url(app, service.url.clone(), bounds).await
}
#[tauri::command]
pub async fn workspace_get(
    app: tauri::AppHandle,
    bridge: State<'_, std::sync::Arc<Bridge>>,
) -> Result<Value, String> {
    let me = get(&bridge, "/api/me").await?["employee"].clone();
    let id = id_of(&me)?;
    let saved = load(&path(&app, &bridge, id)?, id)?;
    Ok(json!({"workspace":saved,"employee":me}))
}
#[tauri::command]
pub async fn workspace_save(
    app: tauri::AppHandle,
    bridge: State<'_, std::sync::Arc<Bridge>>,
    workspace: PersonalWorkspace,
) -> Result<(), String> {
    let me = get(&bridge, "/api/me").await?["employee"].clone();
    let id = id_of(&me)?;
    validate(&workspace, id)?;
    let path = path(&app, &bridge, id)?;
    let parent = path.parent().ok_or("Папка настроек недоступна")?;
    std::fs::create_dir_all(parent).map_err(|_| "Не удалось создать настройки")?;
    let mut temp =
        tempfile::NamedTempFile::new_in(parent).map_err(|_| "Не удалось сохранить настройки")?;
    temp.write_all(
        &serde_json::to_vec_pretty(&workspace).map_err(|_| "Не удалось подготовить настройки")?,
    )
    .and_then(|_| temp.as_file().sync_all())
    .map_err(|_| "Не удалось записать настройки")?;
    temp.persist(path)
        .map_err(|_| "Не удалось сохранить настройки")?;
    Ok(())
}
pub fn text(value: &Value, key: &str) -> String {
    value[key].as_str().unwrap_or("").trim().to_owned()
}
fn list(value: &Value, key: &str) -> Vec<Value> {
    value[key].as_array().cloned().unwrap_or_default()
}
fn demonstration(value: &Value) -> bool {
    let name = text(value, "name").to_uppercase();
    name.starts_with("ДЕМО ·") || name.starts_with("DEMO ·")
}
fn top(values: impl Iterator<Item = String>) -> Vec<String> {
    let mut counts = BTreeMap::new();
    for v in values.filter(|s| !s.is_empty()) {
        *counts.entry(v).or_insert(0) += 1;
    }
    let mut values: Vec<_> = counts.into_iter().collect();
    values.sort_by_key(|(_, n)| std::cmp::Reverse(*n));
    values.into_iter().take(8).map(|(s, _)| s).collect()
}
fn route(c: &Value) -> String {
    let a = text(c, "origin");
    let b = text(c, "destination");
    if a.is_empty() || b.is_empty() {
        String::new()
    } else {
        format!("{a} — {b}")
    }
}
fn derived(clients: &[Value]) -> WorkProfile {
    WorkProfile {
        industries: top(clients.iter().map(|c| text(c, "segment"))),
        regions: top(clients.iter().map(|c| text(c, "region"))),
        routes: top(clients.iter().map(route)),
        available_capacity: 30,
    }
}
fn training_samples(rows: &[Value], owner: &str, profile: &WorkProfile) -> Vec<Sample> {
    let mut seen = HashSet::new();
    rows.iter()
        .filter_map(|company| {
            let id = text(company, "id");
            if text(company, "employee_id") != owner
                || id.is_empty()
                || demonstration(company)
                || !seen.insert(id)
            {
                return None;
            }
            let status = company["lead_status"].as_str()?;
            if !matches!(status, "taken" | "refused") {
                return None;
            }
            Some(Sample {
                features: features(company, profile, 0).0,
                positive: status == "taken",
            })
        })
        .take(10_000)
        .collect()
}
fn matching(values: &[String], value: &str) -> bool {
    let candidate = value.to_lowercase();
    !candidate.is_empty()
        && values.iter().any(|v| {
            let v = v.trim().to_lowercase();
            !v.is_empty() && (candidate.contains(&v) || v.contains(&candidate))
        })
}
fn features(company: &Value, profile: &WorkProfile, load: usize) -> (Vec<f64>, Vec<String>) {
    let industry = matching(&profile.industries, &text(company, "segment"));
    let region = matching(&profile.regions, &text(company, "region"));
    let route = matching(&profile.routes, &route(company));
    let capacity = (profile.available_capacity as usize).saturating_sub(load) as f64
        / (profile.available_capacity as f64).max(1.);
    let mut why = vec![];
    if industry {
        why.push("Совпадает рабочая сфера".into());
    }
    if region {
        why.push("Есть опыт в этом регионе".into());
    }
    if route {
        why.push("Знакомое направление перевозок".into());
    }
    if capacity > 0. {
        why.push(format!(
            "Клиентов в работе: {load}, предел профиля: {}",
            profile.available_capacity
        ));
    }
    (
        vec![
            f64::from(industry),
            f64::from(region),
            f64::from(route),
            capacity,
        ],
        why,
    )
}
#[tauri::command]
pub async fn workspace_recommendations(
    app: tauri::AppHandle,
    bridge: State<'_, std::sync::Arc<Bridge>>,
) -> Result<Value, String> {
    let me = get(&bridge, "/api/me").await?["employee"].clone();
    let id = id_of(&me)?;
    let saved = load(&path(&app, &bridge, id)?, id)?;
    let leader = matches!(me["role"].as_str(), Some("director" | "manager"));
    let mut leads = list(&get(&bridge, "/api/leads").await?, "companies");
    let mut clients = list(
        &get(
            &bridge,
            if leader {
                "/api/clients/overview"
            } else {
                "/api/clients"
            },
        )
        .await?,
        "companies",
    );
    // Existing local test records remain in the database, but must not become
    // employee experience, training evidence or business recommendations.
    leads.retain(|company| !demonstration(company));
    clients.retain(|company| !demonstration(company));
    let people = if leader {
        list(&get(&bridge, "/api/admin/employees").await?, "employees")
    } else {
        vec![me.clone()]
    };
    let mut profiles = vec![];
    for person in people
        .iter()
        .filter(|p| p["active"] != false && p["approved"] != false && p["login"] != "host")
    {
        let owned: Vec<_> = clients
            .iter()
            .filter(|c| c["employee_id"] == person["id"])
            .cloned()
            .collect();
        let mut profile = derived(&owned);
        if person["id"] == me["id"] {
            if !saved.profile.industries.is_empty() {
                profile.industries = saved.profile.industries.clone();
            }
            if !saved.profile.regions.is_empty() {
                profile.regions = saved.profile.regions.clone();
            }
            if !saved.profile.routes.is_empty() {
                profile.routes = saved.profile.routes.clone();
            }
            profile.available_capacity = saved.profile.available_capacity;
        }
        profiles.push((person.clone(), profile, owned.len()));
    }
    if profiles.iter().all(|(p, _, _)| p["id"] != me["id"]) {
        profiles.push((
            me.clone(),
            saved.profile.clone(),
            clients
                .iter()
                .filter(|c| c["employee_id"] == me["id"])
                .count(),
        ));
    }
    let mut ids = HashSet::new();
    let candidates: Vec<_> = leads
        .iter()
        .chain(&clients)
        .filter(|c| {
            ids.insert(text(c, "id"))
                && c["lead_status"] != "refused"
                && c["verification"]["active"] == true
                && nexsearch_core::company::Inn::try_from(text(c, "inn")).is_ok()
        })
        .cloned()
        .collect();
    let profiles_json:Vec<_>=profiles.iter().map(|(p,w,n)|json!({"employee":p,"profile":w,"client_count":n,"source":if p["id"]==me["id"]{"Ваш профиль и доступная рабочая база"}else{"Доступная вам клиентская база сотрудника"}})).collect();
    let training: Vec<_> = profiles
        .iter()
        .map(|(person, profile, _)| {
            let owner = text(person, "id");
            let samples = training_samples(&leads, &owner, profile);
            (owner, samples)
        })
        .collect();
    let mut metadata = vec![];
    let mut matrix = vec![];
    for c in candidates {
        for (person, profile, count) in &profiles {
            if c["employee_id"] == person["id"] && c["lead_status"] == "taken" {
                continue;
            }
            if *count >= profile.available_capacity as usize {
                continue;
            }
            let (f, why) = features(&c, profile, *count);
            if f[..3].iter().all(|v| *v == 0.) {
                continue;
            }
            matrix.push(f);
            metadata.push((c.clone(), person.clone(), why));
        }
    }
    let worker_profiles = profiles_json;
    let ranked=tauri::async_runtime::spawn_blocking(move||->Result<Value,String>{
        let engine=Engine::new(None).map_err(|_|"Вычислительный модуль недоступен")?;let scores=engine.score(&[40.,25.,25.,10.],&matrix).map_err(|_|"Не удалось вычислить подбор")?;
        let models:BTreeMap<_,_>=training.into_iter().filter_map(|(owner,samples)|Model::train(&samples,30).ok().map(|model|(owner,model))).collect();
        let learning=if models.is_empty(){"Правила: для обучения нужны минимум 8 собственных исходов, включая успехи и отказы, с различающимися признаками".to_owned()}else{format!("Обучено профилей: {}. При недостатке личной истории используются правила",models.len())};
        let mut output:Vec<_>=metadata.into_iter().zip(matrix).zip(scores).map(|(((company,employee,mut reasons),f),score)|{let prediction=models.get(&text(&employee,"id")).and_then(|m|m.predict(&f).ok());let adjusted=prediction.map_or(score,|p|score*0.7+p*30.);if prediction.is_some(){reasons.push("Учтены собственные подтверждённые исходы сотрудника".into())}json!({"company":company,"employee":employee,"score":adjusted.round(),"reasons":reasons})}).collect();
        output.sort_by(|a,b|b["score"].as_f64().unwrap_or(0.).total_cmp(&a["score"].as_f64().unwrap_or(0.)));output.truncate(50);
        Ok(json!({"recommendations":output,"profiles":worker_profiles,"learning":learning,"scope":"Доступные вам найденные компании и клиенты. Демонстрационные записи исключены.","checked_at":std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs()}))
    }).await.map_err(|_|"Расчёт прерван".to_owned())??;
    Ok(ranked)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn training_uses_only_own_distinct_confirmed_outcomes() {
        let own = json!({"id":"a","employee_id":"one","name":"Компания","lead_status":"taken","segment":"Металл"});
        let rows = vec![
            own.clone(),
            own,
            json!({"id":"b","employee_id":"two","lead_status":"refused"}),
            json!({"id":"c","employee_id":"one","name":"ДЕМО · тест","lead_status":"refused"}),
            json!({"id":"d","employee_id":"one","lead_status":"new"}),
            json!({"id":"e","employee_id":"one","lead_status":"refused"}),
        ];
        let samples = training_samples(&rows, "one", &WorkProfile::default());
        assert_eq!(samples.len(), 2);
        assert!(samples[0].positive);
        assert!(!samples[1].positive);
    }
    #[test]
    fn explicit_demonstrations_do_not_become_business_evidence() {
        assert!(demonstration(&json!({"name":"ДЕМО · проверка"})));
        assert!(demonstration(&json!({"name":"DEMO · sample"})));
        assert!(!demonstration(&json!({"name":"Демография"})));
        assert!(!demonstration(&json!({"name":"ОАО УГМК"})));
    }
    #[test]
    fn matching_uses_work_facts_and_does_not_invent_industries() {
        let c = json!({"segment":"Производство металла","region":"Москва","origin":"Москва","destination":"Казань"});
        let p = WorkProfile {
            industries: vec!["металл".into()],
            regions: vec!["Москва".into()],
            routes: vec!["Москва — Казань".into()],
            available_capacity: 10,
        };
        let (f, why) = features(&c, &p, 3);
        assert_eq!(f, vec![1., 1., 1., 0.7]);
        assert_eq!(why.len(), 4);
        assert!(!matching(&[], "Москва"));
    }
    #[test]
    fn settings_reject_other_employee_and_duplicate_services() {
        let mut p = PersonalWorkspace::default();
        let id = Uuid::new_v4();
        assert!(validate(&p, id).is_err());
        p.preferences.employee_id = id;
        assert!(validate(&p, id).is_ok());
        p.profile.available_capacity = 1001;
        assert!(validate(&p, id).is_err());
        p.profile.available_capacity = 30;
        let service = nexsearch_core::preferences::PersonalService::new(
            "Тестовый сервис",
            "https://example.org",
        )
        .unwrap();
        p.preferences.services = vec![service.clone(), service];
        assert!(validate(&p, id).is_err());
    }
    #[test]
    fn personal_preferences_roundtrip_and_accounts_are_separate() {
        let home = tempfile::tempdir().unwrap();
        let one = Uuid::new_v4();
        let two = Uuid::new_v4();
        let mut saved = PersonalWorkspace::default();
        saved.preferences.employee_id = one;
        saved.profile.industries = vec!["Техника".into()];
        saved.preferences.services.push(
            nexsearch_core::preferences::PersonalService::new(
                "Личный сервис",
                "https://example.org",
            )
            .unwrap(),
        );
        let file = home.path().join("one.json");
        std::fs::write(&file, serde_json::to_vec(&saved).unwrap()).unwrap();
        let restored = load(&file, one).unwrap();
        assert_eq!(restored.profile.industries, vec!["Техника"]);
        assert_eq!(restored.preferences.services.len(), 1);
        assert!(load(&file, two).is_err());
        assert!(
            load(&home.path().join("two.json"), two)
                .unwrap()
                .preferences
                .services
                .is_empty()
        );
    }
}
