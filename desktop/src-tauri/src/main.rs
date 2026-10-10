// LanternLogic Agent 桌面壳（Tauri 2）—— 窗口加载前端 dist；后端 sidecar 自启自停。
// 打包形态：安装目录 resources/bin/ 下有 agent-shell-backend.exe（PyInstaller -F，
// 冻结态从自身所在目录解析 config.json/data/skills）+ 出厂 config.json + skills。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::Manager;

struct BackendChild(Mutex<Option<Child>>);

fn spawn_backend(app: &tauri::AppHandle) -> Result<Child, String> {
    let resource = app
        .path()
        .resolve("bin/agent-shell-backend.exe", tauri::path::BaseDirectory::Resource)
        .map_err(|e| format!("定位后端失败：{e}"))?;
    if !resource.is_file() {
        return Err(format!("后端不存在：{}", resource.display()));
    }
    // 工作目录 = 后端 exe 所在目录（冻结态 config.json / data/ / skills/ 都按此解析）
    let cwd = resource.parent().ok_or("后端路径异常")?.to_path_buf();
    Command::new(&resource)
        .current_dir(&cwd)
        // 防孤儿：后端自带父进程守护（壳死 → 后端自退），这里把 PID 传给它。
        // RunEvent::Exit 钩子保留作正常退出的快速路径，但不保证所有退出路径触发。
        .env("AGENT_SHELL_PARENT_PID", std::process::id().to_string())
        .spawn()
        .map_err(|e| format!("启动后端失败：{e}"))
}

fn kill_backend(app: &tauri::AppHandle) {
    if let Some(child) = app.state::<BackendChild>().0.lock().unwrap().as_mut() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn main() {
    tauri::Builder::default()
        .manage(BackendChild(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            match spawn_backend(&handle) {
                Ok(child) => {
                    *app.state::<BackendChild>().0.lock().unwrap() = Some(child);
                    Ok(())
                }
                Err(e) => {
                    // 后端起不来也要让窗口开出来显示错误，而不是白屏闪退
                    eprintln!("[agent-shell] {e}");
                    Ok(())
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("LanternLogic Agent 桌面壳启动失败")
        .run(|app, event| {
            // 应用退出路径统一回收后端（WindowEvent::Destroyed 不保证在所有
            // 退出路径上触发——RunEvent::Exit 覆盖优雅关窗与菜单退出）
            if let tauri::RunEvent::Exit = event {
                kill_backend(app);
            }
        });
}
