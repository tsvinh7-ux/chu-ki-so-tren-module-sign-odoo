//go:build windows

package main

import (
	"log"
	"os"
	"path/filepath"
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	moduser32       = windows.NewLazySystemDLL("user32.dll")
	procMessageBoxW = moduser32.NewProc("MessageBoxW")
)

const (
	dialogTitle = "DMAG Ký số"
	idYes       = 6
)

type platformOptions struct {
	autostart bool // started by the Run key at login: stay quiet
}

func messageBox(text string, flags uint32) int {
	t, _ := windows.UTF16PtrFromString(strings.ReplaceAll(text, "\x00", ""))
	c, _ := windows.UTF16PtrFromString(dialogTitle)
	r, _, _ := procMessageBoxW.Call(0, uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(c)),
		uintptr(flags|windows.MB_TOPMOST|windows.MB_SETFOREGROUND))
	return int(r)
}

func showInfo(text string)  { messageBox(text, windows.MB_OK|windows.MB_ICONINFORMATION) }
func showError(text string) { messageBox(text, windows.MB_OK|windows.MB_ICONERROR) }
func askYesNo(text string) bool {
	return messageBox(text, windows.MB_YESNO|windows.MB_ICONQUESTION|windows.MB_DEFBUTTON2) == idYes
}

// installDir is %LOCALAPPDATA%\DMAGKySo.
func installDir() string {
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		if home, err := os.UserHomeDir(); err == nil {
			base = filepath.Join(home, "AppData", "Local")
		}
	}
	return filepath.Join(base, "DMAGKySo")
}

func setupLogging() {
	dir := installDir()
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return
	}
	f, err := openRotatingFile(filepath.Join(dir, "agent.log"), 1<<20)
	if err != nil {
		return
	}
	logger = log.New(f, "", log.LstdFlags)
}

func platformStart(args []string) (platformOptions, bool, int) {
	var opts platformOptions
	var uninstall, runInPlace bool
	for _, a := range args {
		switch strings.ToLower(strings.TrimLeft(a, "-/")) {
		case "uninstall":
			uninstall = true
		case "autostart":
			opts.autostart = true
		case "run":
			// Run from the current location without installing (debugging).
			runInPlace = true
		}
	}
	setupLogging()
	if uninstall {
		doUninstall()
		return opts, true, 0
	}
	if !runInPlace && !runningFromInstallDir() {
		return opts, true, doInstall()
	}
	if !opts.autostart && !runInPlace {
		// Started by hand from the install folder: make sure it will
		// start again at the next login.
		if err := setRunKey(); err != nil {
			logf("không ghi được khóa Run: %v", err)
		}
	}
	return opts, false, 0
}

func platformAlreadyRunning(opts platformOptions) {
	if !opts.autostart {
		showInfo("DMAG Ký số đang chạy.")
	}
}

func platformExtraOrigins() []string { return nil }

func platformStore() (Store, string, error) {
	return newWindowsStore(), "windows-store", nil
}

type windowsPrompter struct{}

func (windowsPrompter) Interactive() bool { return true }

func (windowsPrompter) AskOrigin(origin string) bool {
	msg := "Trang " + origin + " muốn dùng phần mềm DMAG Ký số. Chỉ đồng ý nếu bạn tin cậy trang này. Cho phép?"
	if isInsecureRemoteOrigin(origin) {
		msg += "\n\nCảnh báo: trang này không dùng kết nối bảo mật HTTPS."
	}
	ok := askYesNo(msg)
	logf("hỏi cho phép origin %s: %v", origin, ok)
	return ok
}

func (windowsPrompter) ConfirmSign(origin, document, commonName string) bool {
	return askYesNo("Trang " + origin + " yêu cầu ký tài liệu: " + document +
		"\nChứng thư: " + commonName + "\n\nĐồng ý ký?")
}

func platformPrompter() Prompter { return windowsPrompter{} }
