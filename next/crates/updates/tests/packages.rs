use ed25519_dalek::{Signer, SigningKey, pkcs8::EncodePublicKey};
use nexsearch_updates::{apply, hash_file, prepare, relative, restore, verify_stage};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{fs, io::Write};

fn fixture(
    extra: Option<(&str, &[u8])>,
) -> (tempfile::TempDir, std::path::PathBuf, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let program = dir.path().join("program");
    fs::create_dir(&program).unwrap();
    let key = SigningKey::from_bytes(&[17; 32]);
    fs::write(
        program.join("updates-public.pem"),
        key.verifying_key()
            .to_public_key_pem(Default::default())
            .unwrap(),
    )
    .unwrap();
    fs::write(program.join("package-info.json"), br#"{"version":"1.6.7"}"#).unwrap();
    fs::write(program.join("NexSearch.exe"), b"old").unwrap();
    fs::write(program.join("employee.sec"), b"encrypted-session-must-stay").unwrap();
    let archive = dir.path().join("update.zip");
    let mut files = vec![
        (
            "EmployeeWindows/NexSearch.exe",
            b"new rust executable".as_slice(),
        ),
        (
            "EmployeeWindows/package-info.json",
            br#"{"version":"2.0.0","source_commit":"new"}"#.as_slice(),
        ),
        (
            "EmployeeWindows/native-resource.txt",
            b"new resource".as_slice(),
        ),
    ];
    if let Some(value) = extra {
        files.push(value)
    }
    let hashes = files
        .iter()
        .map(|(n, v)| ((*n).to_owned(), format!("{:x}", Sha256::digest(v))))
        .collect::<std::collections::BTreeMap<_, _>>();
    let raw=serde_json::to_vec(&json!({"version":"2.0.0","role":"EmployeeWindows","schema":19,"source_commit":"new","files":hashes,"runtime":"rust-tauri"})).unwrap();
    let mut zip = zip::ZipWriter::new(fs::File::create(&archive).unwrap());
    let options = zip::write::SimpleFileOptions::default();
    for (name, bytes) in files {
        zip.start_file(name, options).unwrap();
        zip.write_all(bytes).unwrap();
    }
    zip.start_file("release.json", options).unwrap();
    zip.write_all(&raw).unwrap();
    zip.start_file("release.sig", options).unwrap();
    zip.write_all(&key.sign(&raw).to_bytes()).unwrap();
    zip.finish().unwrap();
    (dir, program, archive)
}
#[test]
fn signed_legacy_package_upgrades_and_rolls_back_without_touching_session() {
    let (dir, program, archive) = fixture(None);
    let state = prepare(&archive, &program, dir.path(), "EmployeeWindows").unwrap();
    let backup = apply(&state).unwrap();
    assert_eq!(
        fs::read(program.join("NexSearch.exe")).unwrap(),
        b"new rust executable"
    );
    assert_eq!(
        fs::read(program.join("employee.sec")).unwrap(),
        b"encrypted-session-must-stay"
    );
    restore(&backup).unwrap();
    assert_eq!(fs::read(program.join("NexSearch.exe")).unwrap(), b"old");
    assert!(!program.join("native-resource.txt").exists());
    assert_eq!(
        fs::read(program.join("employee.sec")).unwrap(),
        b"encrypted-session-must-stay"
    );
}
#[test]
fn tampered_stage_is_rejected_before_first_installed_file_changes() {
    let (dir, program, archive) = fixture(None);
    let state = prepare(&archive, &program, dir.path(), "EmployeeWindows").unwrap();
    fs::write(state.parent().unwrap().join("NexSearch.exe"), b"tampered").unwrap();
    assert!(apply(&state).is_err());
    assert_eq!(fs::read(program.join("NexSearch.exe")).unwrap(), b"old");
}
#[test]
fn wrong_role_tampered_archive_and_downgrade_are_rejected() {
    let (dir, program, archive) = fixture(None);
    assert!(prepare(&archive, &program, dir.path(), "HostUbuntu").is_err());
    fs::write(program.join("package-info.json"), br#"{"version":"2.0.1"}"#).unwrap();
    assert!(prepare(&archive, &program, dir.path(), "EmployeeWindows").is_err());
}
#[test]
fn signed_paths_cannot_escape_or_collide_on_windows() {
    for path in [
        "../outside",
        "/absolute",
        "a//b",
        "a/./b",
        "a\\b",
        "C:/test",
        "NUL.txt",
        "a/COM1.exe",
        "a./b",
        "a/b ",
    ] {
        assert!(relative(path).is_err(), "{path}")
    }
    for extra in [
        ("EmployeeWindows/../outside", b"bad".as_slice()),
        ("EmployeeWindows/nexsearch.exe", b"duplicate".as_slice()),
    ] {
        let (dir, program, archive) = fixture(Some(extra));
        assert!(prepare(&archive, &program, dir.path(), "EmployeeWindows").is_err());
        assert_eq!(fs::read(program.join("NexSearch.exe")).unwrap(), b"old");
    }
}
#[test]
fn legacy_python_state_extra_fields_remain_compatible() {
    let (dir, program, archive) = fixture(None);
    let path = prepare(&archive, &program, dir.path(), "EmployeeWindows").unwrap();
    let mut state: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    state["verified_at"] = json!("2026-11-12T20:00:00+00:00");
    nexsearch_updates::atomic_json(&path, &state).unwrap();
    assert_eq!(verify_stage(&path).unwrap().1.version, "2.0.0");
    assert_eq!(
        hash_file(&program.join("NexSearch.exe")).unwrap(),
        format!("{:x}", Sha256::digest(b"old"))
    );
}

#[test]
fn newer_install_between_download_and_exit_is_never_downgraded() {
    let (dir, program, archive) = fixture(None);
    let file = prepare(&archive, &program, dir.path(), "EmployeeWindows").unwrap();
    fs::write(program.join("package-info.json"), br#"{"version":"2.0.1"}"#).unwrap();
    assert!(apply(&file).is_err());
    assert_eq!(fs::read(program.join("NexSearch.exe")).unwrap(), b"old");
}

#[cfg(windows)]
#[test]
fn locked_late_file_restores_earlier_files_and_removes_new_resources() {
    use std::os::windows::fs::OpenOptionsExt;
    let (dir, program, archive) = fixture(None);
    let file = prepare(&archive, &program, dir.path(), "EmployeeWindows").unwrap();
    let _locked = fs::OpenOptions::new()
        .read(true)
        .share_mode(3)
        .open(program.join("package-info.json"))
        .unwrap();
    assert!(apply(&file).is_err());
    assert_eq!(fs::read(program.join("NexSearch.exe")).unwrap(), b"old");
    assert!(!program.join("native-resource.txt").exists());
    assert_eq!(
        fs::read(program.join("employee.sec")).unwrap(),
        b"encrypted-session-must-stay"
    );
}
