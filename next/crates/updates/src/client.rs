//! Native employee updates; retains the existing settings screen contract.
use crate::{atomic_json, prepare, verify_stage};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use chrono::{Timelike, Utc};
use nexsearch_bridge::Bridge;
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
};

pub struct Client {
    pub program: PathBuf,
    pub home: PathBuf,
    bridge: Arc<Bridge>,
    busy: AtomicBool,
    state: Mutex<Value>,
    restart: AtomicBool,
}
fn read(path: &Path) -> Value {
    fs::read(path)
        .ok()
        .and_then(|v| serde_json::from_slice(&v).ok())
        .unwrap_or(json!({}))
}
pub fn employee_home(host_id: &str) -> Option<PathBuf> {
    nexsearch_bridge::session_store::legacy_session_path(host_id)?
        .parent()
        .map(Path::to_owned)
}
pub fn native_store(bridge: &Bridge) -> Option<PathBuf> {
    #[cfg(windows)]
    let base = PathBuf::from(std::env::var_os("APPDATA")?);
    #[cfg(target_os = "macos")]
    let base = PathBuf::from(std::env::var_os("HOME")?).join("Library/Application Support");
    #[cfg(all(not(windows), not(target_os = "macos")))]
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))?;
    Some(
        base.join("ru.nexol.nexsearch")
            .join(bridge.storage_scope())
            .join("session.sec"),
    )
}
impl Client {
    pub fn new(program: PathBuf, home: PathBuf, bridge: Arc<Bridge>) -> Arc<Self> {
        let mut state = read(&home.join("updates/daily.json"));
        state["checking"] = false.into();
        Arc::new(Self {
            program,
            home,
            bridge,
            busy: AtomicBool::new(false),
            state: Mutex::new(state),
            restart: AtomicBool::new(false),
        })
    }
    fn change(&self, fields: Value) {
        let mut value = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if !value.is_object() {
            *value = json!({})
        }
        if let Some(fields) = fields.as_object() {
            for (k, v) in fields {
                value[k] = v.clone()
            }
        }
        let _ = atomic_json(&self.home.join("updates/daily.json"), &*value);
    }
    pub fn status(&self) -> Value {
        let mut value = self.state.lock().unwrap_or_else(|e| e.into_inner()).clone();
        let installed = read(&self.program.join("package-info.json"));
        value["installed_version"] = installed["version"]
            .as_str()
            .unwrap_or(env!("CARGO_PKG_VERSION"))
            .into();
        value["installed_commit"] = installed["source_commit"].clone();
        value["supports_restart"] = true.into();
        value["checking"] = self.busy.load(Ordering::Acquire).into();
        value
    }
    pub fn start(self: &Arc<Self>, force: bool) -> bool {
        if !force {
            let now = Utc::now().with_timezone(&chrono::FixedOffset::east_opt(3 * 3600).unwrap());
            let state = self.status();
            if now.hour() < 10
                || state["date"].as_str() == Some(&now.date_naive().to_string())
                || state["retry_at_unix"].as_i64().unwrap_or(0) > Utc::now().timestamp()
            {
                return false;
            }
        }
        if self.busy.swap(true, Ordering::AcqRel) {
            return false;
        }
        let lock = (|| -> std::io::Result<fs::File> {
            fs::create_dir_all(self.home.join("updates"))?;
            let file = fs::OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .open(self.home.join("updates/check.lock"))?;
            file.try_lock().map_err(std::io::Error::other)?;
            Ok(file)
        })();
        let Ok(lock) = lock else {
            self.busy.store(false, Ordering::Release);
            return false;
        };
        let this = self.clone();
        tokio::spawn(async move {
            let _lock = lock;
            this.change(json!({"phase":"checking","message":"Проверяем пакет сотрудника на хосте","checked_at":Utc::now().to_rfc3339(),"retry_at_unix":Utc::now().timestamp()+300}));
            if let Err(error) = this.check().await {
                this.change(json!({"phase":"error","message":error,"date":null}));
            }
            this.busy.store(false, Ordering::Release);
        });
        true
    }
    async fn check(&self) -> Result<(), String> {
        let role = if cfg!(target_os = "macos") {
            "EmployeeMac"
        } else {
            "EmployeeWindows"
        };
        let reply = self
            .bridge
            .request(&format!("/api/update/info?platform={role}"), "GET", None)
            .await;
        let info: Value = serde_json::from_slice(
            &STANDARD
                .decode(reply.body)
                .map_err(|_| "Некорректный ответ обновления")?,
        )
        .map_err(|_| "Некорректный ответ обновления")?;
        if reply.status != 200 {
            return Err(info["error"].as_str().unwrap_or("Хост недоступен").into());
        }
        if info["available"] != true {
            self.change(json!({"phase":"waiting","message":"Хост ещё не подготовил обновление. Повторим через 5 минут"}));
            return Ok(());
        }
        let version = info["version"]
            .as_str()
            .ok_or("В пакете не указана версия")?;
        let commit = info["source_commit"]
            .as_str()
            .ok_or("В пакете не указана ревизия")?;
        let installed = read(&self.program.join("package-info.json"));
        let incoming =
            semver::Version::parse(version).map_err(|_| "Некорректная версия обновления")?;
        let current = semver::Version::parse(
            installed["version"]
                .as_str()
                .unwrap_or(env!("CARGO_PKG_VERSION")),
        )
        .map_err(|_| "Некорректная установленная версия")?;
        self.change(
            json!({"version":version,"source_commit":commit,"host_checked_at":info["checked_at"]}),
        );
        if incoming < current || installed["source_commit"].as_str() == Some(commit) {
            let moscow = Utc::now().with_timezone(&chrono::FixedOffset::east_opt(10800).unwrap());
            let fresh = info["checked_at"]
                .as_str()
                .and_then(|v| chrono::DateTime::parse_from_rfc3339(v).ok())
                .is_some_and(|v| {
                    v.with_timezone(moscow.offset()).date_naive() == moscow.date_naive()
                        && v.with_timezone(moscow.offset()).hour() >= 10
                });
            self.change(json!({"phase":"current","message":"Установлена актуальная сборка","pending_state":null,"pending_commit":null,"date":if fresh {Some(moscow.date_naive().to_string())}else{None}}));
            return Ok(());
        }
        let prior = self.status();
        if prior["pending_commit"].as_str() == Some(commit)
            && let Some(path) = prior["pending_state"].as_str()
            && verify_stage(Path::new(path)).is_ok()
        {
            self.change(json!({"phase":"ready","message":"Обновление готово. Можно установить и перезапустить"}));
            return Ok(());
        }
        let digest = info["sha256"]
            .as_str()
            .ok_or("Нет контрольной суммы пакета")?;
        let size = info["size_bytes"].as_u64().ok_or("Нет размера пакета")?;
        let archive = self
            .home
            .join("updates")
            .join(format!("employee-{}.zip", uuid::Uuid::new_v4()));
        self.change(json!({"phase":"downloading","message":"Скачиваем обновление с хоста","total_bytes":size,"downloaded_bytes":0}));
        self.bridge
            .download_update(role, digest, size, &archive, |n| {
                self.change(json!({"downloaded_bytes":n}))
            })
            .await?;
        self.change(json!({"phase":"verifying","message":"Проверяем подпись и готовим установку"}));
        let (source, program, home) = (archive.clone(), self.program.clone(), self.home.clone());
        let result = tokio::task::spawn_blocking(move || {
            prepare(&source, &program, &home, role).map_err(|e| e.to_string())
        })
        .await
        .map_err(|_| "Подготовка обновления прервана")?;
        let _ = fs::remove_file(&archive);
        let file = result?;
        let (_, manifest) = verify_stage(&file).map_err(|e| e.to_string())?;
        if manifest.version != version || manifest.source_commit != commit {
            return Err("Подписанные метаданные не совпали с ответом хоста".into());
        }
        self.change(json!({"phase":"ready","message":"Обновление готово. Можно установить и перезапустить","pending_state":file,"pending_commit":commit}));
        Ok(())
    }
    pub fn request_install(&self) -> Result<(), String> {
        let state = self.status();
        let path = state["pending_state"]
            .as_str()
            .ok_or("Сначала дождитесь подготовки обновления")?;
        let (prepared, manifest) = verify_stage(Path::new(path)).map_err(|e| e.to_string())?;
        if prepared.program
            != self
                .program
                .canonicalize()
                .map_err(|_| "Недоступна папка программы")?
            || state["pending_commit"].as_str() != Some(&manifest.source_commit)
        {
            return Err("Обновление относится к другой программе".into());
        }
        self.restart.store(true, Ordering::Release);
        Ok(())
    }
    pub fn launch_pending(&self) -> Result<bool, String> {
        let state = self.status();
        let Some(file) = state["pending_state"].as_str() else {
            return Ok(false);
        };
        let (prepared, _) = verify_stage(Path::new(file)).map_err(|e| e.to_string())?;
        if prepared.program
            != self
                .program
                .canonicalize()
                .map_err(|_| "Недоступна папка программы")?
        {
            return Err("Неподходящая папка установки".into());
        }
        let name = if cfg!(windows) {
            "NexSearch Maintenance.exe"
        } else {
            "NexSearch Maintenance"
        };
        let helper = self.program.join(name);
        let runner = self.home.join("updates").join(format!(
            "runner-{}{}",
            uuid::Uuid::new_v4(),
            if cfg!(windows) { ".exe" } else { "" }
        ));
        fs::copy(helper, &runner).map_err(|_| "Не удалось запустить установщик")?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&runner, fs::Permissions::from_mode(0o700))
                .map_err(|_| "Установщик недоступен")?;
        }
        let mut command = std::process::Command::new(runner);
        command
            .args(["apply", "--state", file, "--home"])
            .arg(&self.home)
            .arg("--wait-pid")
            .arg(std::process::id().to_string());
        if self.restart.load(Ordering::Acquire) {
            command.arg("--restart");
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000008);
        }
        command
            .spawn()
            .map_err(|_| "Не удалось запустить установщик")?;
        Ok(true)
    }
}
