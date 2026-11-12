//! Verifies the exact Ed25519 ZIP format already understood by employee 1.6.7.
//! Only program files are replaced. Sessions, personal preferences and DB stay outside.
use anyhow::{Context, Result, bail, ensure};
use ed25519_dalek::{Signature, VerifyingKey, pkcs8::DecodePublicKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
};

pub const MAX_ARCHIVE: u64 = 256 * 1024 * 1024;
pub mod client;
const MAX_EXPANDED: u64 = 2 * 1024 * 1024 * 1024;
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Manifest {
    pub version: String,
    pub source_commit: String,
    pub role: String,
    pub schema: u32,
    #[serde(default)]
    pub built_at: u64,
    pub files: BTreeMap<String, String>,
    #[serde(default)]
    pub runtime: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Prepared {
    pub program: PathBuf,
    pub stage: PathBuf,
    pub role: String,
    pub version: String,
    pub files: BTreeMap<String, String>,
    pub verified_at: String,
}
#[derive(Serialize, Deserialize)]
pub struct Rollback {
    pub program: PathBuf,
    pub files: Vec<String>,
    /// Absent in old Python rollback records: those records only restore originals.
    #[serde(default)]
    pub created: Vec<String>,
}
pub fn role_valid(role: &str) -> bool {
    matches!(
        role,
        "EmployeeWindows" | "EmployeeMac" | "HostUbuntu" | "HostWindows"
    )
}
pub fn relative(name: &str) -> Result<PathBuf> {
    ensure!(
        !name.is_empty() && name.len() <= 1000,
        "Недопустимый путь обновления"
    );
    ensure!(
        !name.contains(['\\', ':']) && !name.chars().any(char::is_control),
        "Недопустимый путь обновления"
    );
    let mut path = PathBuf::new();
    for part in name.split('/') {
        ensure!(
            !part.is_empty() && part != "." && part != ".." && !part.ends_with(['.', ' ']),
            "Недопустимый путь обновления"
        );
        let stem = part.split('.').next().unwrap_or("").to_ascii_uppercase();
        ensure!(
            !matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
                && !(1..=9).any(|i| stem == format!("COM{i}") || stem == format!("LPT{i}")),
            "Зарезервированное имя файла"
        );
        path.push(part);
    }
    Ok(path)
}
pub fn target(base: &Path, name: &str) -> Result<PathBuf> {
    let rel = relative(name)?;
    let mut path = base.to_owned();
    for component in rel.components() {
        path.push(component);
        if let Ok(meta) = fs::symlink_metadata(&path) {
            ensure!(
                !meta.file_type().is_symlink(),
                "Ссылки в папке обновления запрещены"
            );
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                ensure!(
                    meta.file_attributes() & 0x400 == 0,
                    "Точки повторного анализа запрещены"
                );
            }
        }
    }
    Ok(path)
}
pub fn hash_file(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut buffer = [0; 65536];
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
fn signed(raw: &[u8], signature: &[u8], public: &[u8], role: &str) -> Result<Manifest> {
    ensure!(
        role_valid(role) && raw.len() < 8 * 1024 * 1024,
        "Неподходящий пакет"
    );
    let key = VerifyingKey::from_public_key_pem(std::str::from_utf8(public)?)?;
    key.verify_strict(raw, &Signature::from_slice(signature)?)
        .context("Подпись обновления не подтверждена")?;
    let manifest: Manifest = serde_json::from_slice(raw)?;
    ensure!(
        manifest.role == role
            && manifest.schema >= 19
            && !manifest.files.is_empty()
            && manifest.files.len() <= 20000,
        "Пакет не подходит установленной программе"
    );
    semver::Version::parse(&manifest.version)?;
    ensure!(
        !manifest.source_commit.is_empty() && manifest.source_commit.len() <= 100,
        "Отсутствует ревизия пакета"
    );
    let mut names = BTreeSet::new();
    for (name, digest) in &manifest.files {
        let rel = name
            .strip_prefix(&format!("{role}/"))
            .context("Файл относится к другой роли")?;
        relative(rel)?;
        ensure!(
            names.insert(rel.to_lowercase()),
            "Повторяющиеся имена в пакете"
        );
        ensure!(
            digest.len() == 64
                && digest
                    .bytes()
                    .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()),
            "Некорректная контрольная сумма"
        );
    }
    Ok(manifest)
}
pub fn atomic_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let parent = path.parent().context("Папка состояния недоступна")?;
    fs::create_dir_all(parent)?;
    let mut temp = tempfile::NamedTempFile::new_in(parent)?;
    serde_json::to_writer_pretty(&mut temp, value)?;
    temp.flush()?;
    temp.as_file().sync_all()?;
    temp.persist(path).map_err(|e| e.error)?;
    Ok(())
}
pub fn prepare(archive: &Path, program: &Path, home: &Path, role: &str) -> Result<PathBuf> {
    let program = program.canonicalize()?;
    ensure!(
        fs::metadata(archive)?.len() <= MAX_ARCHIVE,
        "Пакет слишком большой"
    );
    let public = fs::read(program.join("updates-public.pem"))?;
    let mut zip = zip::ZipArchive::new(File::open(archive)?)?;
    let read_small = |zip: &mut zip::ZipArchive<File>, name: &str, max: u64| -> Result<Vec<u8>> {
        let mut entry = zip.by_name(name)?;
        ensure!(entry.size() <= max, "Некорректные метаданные пакета");
        let mut data = Vec::new();
        entry.read_to_end(&mut data)?;
        Ok(data)
    };
    let raw = read_small(&mut zip, "release.json", 8 * 1024 * 1024)?;
    let signature = read_small(&mut zip, "release.sig", 64)?;
    let manifest = signed(&raw, &signature, &public, role)?;
    if let Ok(installed) = fs::read(program.join("package-info.json")) {
        let old: serde_json::Value = serde_json::from_slice(&installed)?;
        let version = semver::Version::parse(old["version"].as_str().unwrap_or("0.0.0"))?;
        ensure!(
            semver::Version::parse(&manifest.version)? >= version,
            "Понижение версии запрещено"
        );
        if old["version"].as_str() == Some(&manifest.version) {
            ensure!(
                manifest.built_at >= old["built_at"].as_u64().unwrap_or(0),
                "Пакет старее установленной сборки"
            );
        }
    }
    let mut seen = BTreeSet::new();
    let mut total = 0u64;
    for i in 0..zip.len() {
        let entry = zip.by_index(i)?;
        let name = entry.name()?.into_owned();
        ensure!(seen.insert(name.clone()), "Дублирующаяся запись ZIP");
        ensure!(
            entry.unix_mode().unwrap_or(0) & 0o170000 != 0o120000,
            "Ссылки в пакете запрещены"
        );
        ensure!(
            name == "release.json" || name == "release.sig" || manifest.files.contains_key(&name),
            "Архив содержит неподписанный файл"
        );
        total = total
            .checked_add(entry.size())
            .context("Пакет слишком большой")?;
        ensure!(
            total <= MAX_EXPANDED,
            "Пакет слишком большой после распаковки"
        );
    }
    ensure!(
        seen.len() == manifest.files.len() + 2,
        "В пакете отсутствуют подписанные файлы"
    );
    let updates = home.join("updates");
    fs::create_dir_all(&updates)?;
    let stage = tempfile::Builder::new()
        .prefix("stage-")
        .tempdir_in(&updates)?;
    let mut files = BTreeMap::new();
    for (name, digest) in &manifest.files {
        let rel = &name[role.len() + 1..];
        let path = target(stage.path(), rel)?;
        fs::create_dir_all(path.parent().context("Некорректный путь")?)?;
        let mut output = File::create(&path)?;
        let mut entry = zip.by_name(name)?;
        std::io::copy(&mut entry, &mut output)?;
        output.sync_all()?;
        ensure!(
            hash_file(&path)? == *digest,
            "Контрольная сумма файла не совпала"
        );
        if rel == "updates-public.pem" {
            let incoming = VerifyingKey::from_public_key_pem(&fs::read_to_string(&path)?)?;
            let current = VerifyingKey::from_public_key_pem(std::str::from_utf8(&public)?)?;
            ensure!(incoming == current, "Замена доверенного ключа запрещена");
        }
        files.insert(rel.to_owned(), digest.clone());
    }
    fs::write(stage.path().join("release.json"), &raw)?;
    fs::write(stage.path().join("release.sig"), &signature)?;
    let state = Prepared {
        program,
        stage: stage.path().canonicalize()?,
        role: role.into(),
        version: manifest.version,
        files,
        verified_at: String::new(),
    };
    atomic_json(&stage.path().join("update-state.json"), &state)?;
    Ok(stage.keep().join("update-state.json"))
}
pub fn verify_stage(state_file: &Path) -> Result<(Prepared, Manifest)> {
    let state: Prepared = serde_json::from_slice(&fs::read(state_file)?)?;
    let stage = state.stage.canonicalize()?;
    let program = state.program.canonicalize()?;
    ensure!(
        state_file.canonicalize()?.parent() == Some(stage.as_path())
            && stage != program
            && !program.starts_with(&stage),
        "Некорректная папка подготовки"
    );
    let manifest = signed(
        &fs::read(stage.join("release.json"))?,
        &fs::read(stage.join("release.sig"))?,
        &fs::read(program.join("updates-public.pem"))?,
        &state.role,
    )?;
    if let Ok(bytes) = fs::read(program.join("package-info.json")) {
        let installed: serde_json::Value = serde_json::from_slice(&bytes)?;
        let current = semver::Version::parse(installed["version"].as_str().unwrap_or("0.0.0"))?;
        ensure!(
            semver::Version::parse(&manifest.version)? >= current,
            "Подготовленный пакет старее установленной версии"
        );
        if installed["version"].as_str() == Some(&manifest.version) {
            ensure!(
                manifest.built_at >= installed["built_at"].as_u64().unwrap_or(0),
                "Подготовленный пакет старее установленной сборки"
            );
        }
    }
    let files: BTreeMap<_, _> = manifest
        .files
        .iter()
        .map(|(n, v)| (n[state.role.len() + 1..].to_owned(), v.clone()))
        .collect();
    ensure!(
        files == state.files && manifest.version == state.version,
        "Список подготовки изменён"
    );
    for (name, digest) in &files {
        ensure!(
            hash_file(&target(&stage, name)?)? == *digest,
            "Подготовленные файлы изменены"
        );
        target(&program, name)?;
    }
    Ok((state, manifest))
}
fn executable(name: &str) -> bool {
    matches!(
        name,
        "NexSearch" | "NexSearch Maintenance" | "nexsearch-host" | "nexsearch-maintenance"
    ) || name.starts_with("bin/")
}
fn replace(source: &Path, destination: &Path, executable: bool) -> Result<()> {
    let parent = destination.parent().context("Некорректный путь")?;
    fs::create_dir_all(parent)?;
    let mut temp = tempfile::NamedTempFile::new_in(parent)?;
    std::io::copy(&mut File::open(source)?, &mut temp)?;
    temp.as_file().sync_all()?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        temp.as_file()
            .set_permissions(fs::Permissions::from_mode(if executable {
                0o755
            } else {
                0o600
            }))?;
    }
    #[cfg(not(unix))]
    let _ = executable;
    temp.persist(destination).map_err(|e| e.error)?;
    Ok(())
}
pub fn apply(state_file: &Path) -> Result<PathBuf> {
    let (state, _) = verify_stage(state_file)?;
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(state.program.join(".nexsearch-install.lock"))?;
    lock.try_lock()
        .context("Другой установщик уже обновляет программу")?;
    verify_stage(state_file)?;
    let folder = state
        .stage
        .parent()
        .context("Нет папки обновления")?
        .join(format!("rollback-{}", uuid::Uuid::new_v4()));
    fs::create_dir(&folder)?;
    let mut rollback = Rollback {
        program: state.program.clone(),
        files: Vec::new(),
        created: Vec::new(),
    };
    // Capture every original before changing the first program file.
    for name in state.files.keys() {
        let destination = target(&state.program, name)?;
        if destination.exists() {
            let backup = target(&folder, name)?;
            fs::create_dir_all(backup.parent().context("Нет папки резервной копии")?)?;
            fs::copy(&destination, &backup)?;
        } else {
            rollback.created.push(name.clone());
        }
        rollback.files.push(name.clone());
    }
    atomic_json(&folder.join("rollback-state.json"), &rollback)?;
    for name in state.files.keys() {
        if let Err(error) = replace(
            &target(&state.stage, name)?,
            &target(&state.program, name)?,
            executable(name),
        ) {
            restore(&folder).context("Ошибка восстановления предыдущих файлов")?;
            bail!("Установка отменена, предыдущая версия восстановлена: {error}");
        }
    }
    Ok(folder)
}
pub fn restore(folder: &Path) -> Result<()> {
    let state: Rollback = serde_json::from_slice(&fs::read(folder.join("rollback-state.json"))?)?;
    for name in &state.files {
        let source = target(folder, name)?;
        let destination = target(&state.program, name)?;
        if source.is_file() {
            replace(&source, &destination, executable(name))?;
        } else if state.created.contains(name) && destination.is_file() {
            fs::remove_file(destination)?;
        }
    }
    Ok(())
}
