#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use anyhow::{Context, Result, ensure};
use std::{
    fs,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

fn argument(args: &[String], name: &str) -> Option<String> {
    args.windows(2)
        .find(|pair| pair[0] == name)
        .map(|pair| pair[1].clone())
}
fn required(args: &[String], name: &str) -> Result<PathBuf> {
    Ok(PathBuf::from(
        argument(args, name).with_context(|| format!("Не указан {name}"))?,
    ))
}
fn restart(program: &Path, home: Option<&Path>) -> Result<()> {
    let executable = program.join(if cfg!(windows) {
        "NexSearch.exe"
    } else {
        "NexSearch"
    });
    let mut command = std::process::Command::new(executable);
    command.env_remove("NEXSEARCH_LEGACY_HANDOFF");
    command.current_dir(program);
    if let Some(home) = home {
        command.env("NEXOL_HOME", home);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command
        .spawn()
        .context("Программа установлена; автоматический запуск не удался")?;
    Ok(())
}
fn run() -> Result<serde_json::Value> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let action = args.first().context("Не указано действие")?;
    match action.as_str() {
        "check-daily" => {
            let program = required(&args, "--program")?;
            let home = required(&args, "--home")?;
            let trust: nexsearch_bridge::Trust =
                serde_json::from_slice(&fs::read(program.join("client-trust.json"))?)?;
            let bridge = std::sync::Arc::new(
                nexsearch_bridge::Bridge::new(trust).map_err(anyhow::Error::msg)?,
            );
            let runtime = tokio::runtime::Runtime::new()?;
            runtime.block_on(async {
                let _ = bridge.load_internet_profile(&program).await;
                let store = nexsearch_updates::client::native_store(&bridge)
                    .context("Недоступно хранилище сеанса")?;
                bridge
                    .configure_store(store)
                    .await
                    .map_err(anyhow::Error::msg)?;
                bridge.prepare_legacy_session().await;
                bridge.reconnect_saved().await.map_err(anyhow::Error::msg)?;
                if !bridge.has_session().await {
                    bridge
                        .import_session(&home.join("employee.sec"))
                        .await
                        .map_err(anyhow::Error::msg)?;
                }
                let updater = nexsearch_updates::client::Client::new(program, home.clone(), bridge);
                if updater.start(args.iter().any(|s| s == "--force")) {
                    while updater.status()["checking"] == true {
                        tokio::time::sleep(Duration::from_millis(200)).await;
                    }
                    let instance = fs::OpenOptions::new()
                        .read(true)
                        .write(true)
                        .create(true)
                        .truncate(false)
                        .open(home.join("instance.lock"))?;
                    if instance.try_lock().is_ok() {
                        updater.launch_pending().map_err(anyhow::Error::msg)?;
                    }
                }
                Ok(updater.status())
            })
        }
        "prepare" => {
            let file = nexsearch_updates::prepare(
                &required(&args, "--archive")?,
                &required(&args, "--program")?,
                &required(&args, "--home")?,
                &argument(&args, "--role").context("Не указана роль")?,
            )?;
            Ok(serde_json::json!({"verified":true,"state_file":file}))
        }
        "apply" => {
            let file = required(&args, "--state")?;
            let (prepared, manifest) = nexsearch_updates::verify_stage(&file)?;
            // Verify before waiting, then apply verifies again after the old application exits.
            if let Some(pid) = argument(&args, "--wait-pid") {
                let pid: u32 = pid.parse()?;
                ensure!(pid != std::process::id(), "Неверный процесс ожидания");
                let until = Instant::now() + Duration::from_secs(180);
                let mut processes = sysinfo::System::new();
                let pid = sysinfo::Pid::from_u32(pid);
                loop {
                    processes.refresh_processes(sysinfo::ProcessesToUpdate::Some(&[pid]), true);
                    if processes.process(pid).is_none() {
                        break;
                    }
                    ensure!(
                        Instant::now() < until,
                        "Программа ещё работает. Закройте её и повторите обновление"
                    );
                    std::thread::sleep(Duration::from_millis(200));
                }
            }
            let result = nexsearch_updates::apply(&file);
            let home = argument(&args, "--home").map(PathBuf::from);
            match result {
                Ok(rollback) => {
                    if let Some(home) = &home {
                        let path = home.join("updates/daily.json");
                        let mut state: serde_json::Value = fs::read(&path)
                            .ok()
                            .and_then(|v| serde_json::from_slice(&v).ok())
                            .unwrap_or(serde_json::json!({}));
                        for key in ["pending_state", "pending_commit", "restart_commit"] {
                            state
                                .as_object_mut()
                                .context("Повреждено состояние обновления")?
                                .remove(key);
                        }
                        state["phase"] = "installed".into();
                        state["installed_commit"] = manifest.source_commit.into();
                        state["message"] = "Обновление установлено".into();
                        nexsearch_updates::atomic_json(&path, &state)?;
                    }
                    if args.iter().any(|s| s == "--restart") {
                        restart(&prepared.program, home.as_deref())?
                    }
                    Ok(serde_json::json!({"installed":manifest.version,"rollback":rollback}))
                }
                Err(error) => {
                    if args.iter().any(|s| s == "--restart") {
                        let _ = restart(&prepared.program, home.as_deref());
                    }
                    Err(error)
                }
            }
        }
        "rollback" => {
            nexsearch_updates::restore(&required(&args, "--folder")?)?;
            Ok(serde_json::json!({"restored":true}))
        }
        _ => anyhow::bail!("Неизвестное действие установщика"),
    }
}
fn main() {
    match run() {
        Ok(value) => println!("{value}"),
        Err(error) => {
            eprintln!("{}", serde_json::json!({"error":error.to_string()}));
            std::process::exit(1)
        }
    }
}
