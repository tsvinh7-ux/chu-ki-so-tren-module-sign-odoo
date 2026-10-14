//go:build windows

package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

const (
	exeName    = "DMAGKySo.exe"
	runKeyPath = `Software\Microsoft\Windows\CurrentVersion\Run`
	runValue   = "DMAGKySo"
)

func installedExe() string { return filepath.Join(installDir(), exeName) }

func samePath(a, b string) bool {
	clean := func(p string) string {
		if r, err := filepath.EvalSymlinks(p); err == nil {
			p = r
		}
		if abs, err := filepath.Abs(p); err == nil {
			p = abs
		}
		return filepath.Clean(p)
	}
	return strings.EqualFold(clean(a), clean(b))
}

func runningFromInstallDir() bool {
	self, err := os.Executable()
	if err != nil {
		return true // cannot tell: just run
	}
	return samePath(self, installedExe())
}

func setRunKey() error {
	k, _, err := registry.CreateKey(registry.CURRENT_USER, runKeyPath, registry.SET_VALUE)
	if err != nil {
		return err
	}
	defer k.Close()
	return k.SetStringValue(runValue, `"`+installedExe()+`" /autostart`)
}

func deleteRunKey() error {
	k, err := registry.OpenKey(registry.CURRENT_USER, runKeyPath, registry.SET_VALUE)
	if err != nil {
		return err
	}
	defer k.Close()
	err = k.DeleteValue(runValue)
	if err == registry.ErrNotExist {
		return nil
	}
	return err
}

func agentAddr() string { return net.JoinHostPort("127.0.0.1", strconv.Itoa(listenPort())) }

func portBusy() bool {
	c, err := net.DialTimeout("tcp", agentAddr(), 500*time.Millisecond)
	if err != nil {
		return false
	}
	c.Close()
	return true
}

func waitPort(busy bool, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if portBusy() == busy {
			return true
		}
		time.Sleep(200 * time.Millisecond)
	}
	return portBusy() == busy
}

// runningVersion asks a running agent for its version ("" if unknown).
func runningVersion() string {
	cl := &http.Client{Timeout: 2 * time.Second}
	resp, err := cl.Get("http://" + agentAddr() + "/v1/info")
	if err != nil {
		return ""
	}
	defer resp.Body.Close()
	var info struct {
		Name    string `json:"name"`
		Version string `json:"version"`
	}
	if json.NewDecoder(io.LimitReader(resp.Body, 4096)).Decode(&info) != nil || info.Name != agentName {
		return ""
	}
	return info.Version
}

// stopRunning asks a running agent (an older version) to exit.
func stopRunning() {
	cl := &http.Client{Timeout: 2 * time.Second}
	req, _ := http.NewRequest(http.MethodPost, "http://"+agentAddr()+"/v1/shutdown", nil)
	req.Header.Set("X-DMAG-Shutdown", "1")
	if resp, err := cl.Do(req); err == nil {
		resp.Body.Close()
	}
	waitPort(false, 5*time.Second)
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	tmp := dst + ".new"
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o755)
	if err != nil {
		return err
	}
	// io.Copy copies only the main data stream, so the "downloaded from the
	// Internet" mark (Zone.Identifier stream) is not carried over.
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		os.Remove(tmp)
		return err
	}
	if err := out.Close(); err != nil {
		os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, dst); err != nil {
		// The target may be locked by a running copy: a running exe can
		// still be renamed out of the way.
		old := dst + ".old"
		os.Remove(old)
		if e := os.Rename(dst, old); e != nil {
			os.Remove(tmp)
			return err
		}
		if e := os.Rename(tmp, dst); e != nil {
			os.Remove(tmp)
			return e
		}
	}
	return nil
}

func startDetached(path string, args ...string) error {
	cmd := exec.Command(path, args...)
	cmd.Dir = filepath.Dir(path)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: windows.DETACHED_PROCESS | windows.CREATE_NEW_PROCESS_GROUP,
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	return cmd.Process.Release()
}

// doInstall copies the running exe to %LOCALAPPDATA%\DMAGKySo, registers it
// in HKCU\...\Run (no admin rights needed) and starts it.
func doInstall() int {
	self, err := os.Executable()
	if err != nil {
		showError("Không cài đặt được DMAG Ký số: " + err.Error())
		return 1
	}
	target := installedExe()
	logf("cài đặt %s -> %s", self, target)

	updated := false
	if portBusy() {
		v := runningVersion()
		if v == version {
			if _, err := os.Stat(target); err == nil {
				_ = setRunKey()
				showInfo("DMAG Ký số đang chạy.")
				return 0
			}
		}
		// Another (older/newer or unknown) version is running: stop it.
		stopRunning()
		updated = v != ""
	}

	if err := os.MkdirAll(installDir(), 0o700); err != nil {
		showError("Không tạo được thư mục cài đặt: " + err.Error())
		return 1
	}
	os.Remove(target + ".old") // leftover from a previous update
	if err := copyFile(self, target); err != nil {
		showError("Không sao chép được DMAG Ký số vào " + installDir() + ": " + err.Error())
		return 1
	}
	if err := setRunKey(); err != nil {
		logf("không ghi được khóa Run: %v", err)
		showError("Không đăng ký được chế độ tự chạy khi bật máy: " + err.Error())
	}
	if portBusy() {
		// The old version did not stop (for example version 1.0.0 being
		// replaced by itself while busy). It will be replaced at next login.
		showInfo(fmt.Sprintf("Đã cập nhật DMAG Ký số %s. Phiên bản mới sẽ chạy sau khi bạn đăng xuất hoặc khởi động lại máy.", version))
		return 0
	}
	if err := startDetached(target, "/autostart"); err != nil {
		showError("Đã cài đặt nhưng không khởi chạy được DMAG Ký số: " + err.Error())
		return 1
	}
	if !waitPort(true, 8*time.Second) {
		logf("bản cài đặt chưa mở cổng sau 8 giây")
	}
	if updated {
		showInfo(fmt.Sprintf("Đã cập nhật DMAG Ký số lên phiên bản %s. Bạn có thể quay lại trang ký hợp đồng.", version))
	} else {
		showInfo("Đã cài đặt DMAG Ký số. Phần mềm sẽ tự chạy khi bật máy. Bạn có thể quay lại trang ký hợp đồng.")
	}
	return 0
}

// doUninstall removes the auto-start entry and optionally the settings.
func doUninstall() {
	if err := deleteRunKey(); err != nil {
		logf("xóa khóa Run lỗi: %v", err)
	}
	if portBusy() {
		stopRunning()
	}
	if askYesNo("Xóa luôn danh sách các trang web đã được cho phép sử dụng DMAG Ký số?") {
		if p, err := configPath(); err == nil {
			os.Remove(p)
			os.Remove(filepath.Dir(p))
		}
	}
	showInfo("Đã gỡ DMAG Ký số khỏi chế độ tự chạy khi bật máy.\n\n" +
		"Nếu phần mềm vẫn còn chạy, hãy đăng xuất/khởi động lại máy hoặc kết thúc tiến trình DMAGKySo.exe trong Task Manager.\n" +
		"Bạn có thể xóa thư mục " + installDir() + " để gỡ hoàn toàn.")
}
