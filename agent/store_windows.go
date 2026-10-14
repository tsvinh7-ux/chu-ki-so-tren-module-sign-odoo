//go:build windows

package main

// Windows backend: certificates of the CurrentUser\MY store. USB token
// drivers of Vietnamese CAs (VNPT, Viettel, BKAV, FPT, MISA, CA2, ...)
// register the token certificates there and attach a CSP (legacy
// CryptoAPI) or KSP (CNG) to them. Signing goes through
// CryptAcquireCertificatePrivateKey, so the token driver shows its own PIN
// dialog.
//
// NOTE: this file is type-checked by cross compilation but has NOT been
// executed against real tokens; see README.md ("cần kiểm thử trên máy
// Windows có USB token thật").

import (
	"errors"
	"fmt"
	"runtime"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	modcrypt32 = windows.NewLazySystemDLL("crypt32.dll")
	modadvapi  = windows.NewLazySystemDLL("advapi32.dll")
	modncrypt  = windows.NewLazySystemDLL("ncrypt.dll")

	procCertGetCertificateContextProperty = modcrypt32.NewProc("CertGetCertificateContextProperty")

	procCryptCreateHash     = modadvapi.NewProc("CryptCreateHash")
	procCryptSetHashParam   = modadvapi.NewProc("CryptSetHashParam")
	procCryptSignHashW      = modadvapi.NewProc("CryptSignHashW")
	procCryptDestroyHash    = modadvapi.NewProc("CryptDestroyHash")
	procCryptSetProvParam   = modadvapi.NewProc("CryptSetProvParam")
	procNCryptSignHash      = modncrypt.NewProc("NCryptSignHash")
	procNCryptFreeObject    = modncrypt.NewProc("NCryptFreeObject")
	procNCryptSetProperty   = modncrypt.NewProc("NCryptSetProperty")
	procGetForegroundWindow = moduser32.NewProc("GetForegroundWindow")
)

const (
	certKeyProvInfoPropID = 2 // CERT_KEY_PROV_INFO_PROP_ID

	calgSHA256   = 0x0000800c
	hpHashVal    = 0x0002
	ppClientHWND = 1 // PP_CLIENT_HWND

	bcryptPadPKCS1 = 0x00000002

	// HRESULT / Win32 error codes (as uint32).
	errCancelled          = 1223       // ERROR_CANCELLED
	errMoreData           = 234        // ERROR_MORE_DATA
	hrCancelled           = 0x800704C7 // HRESULT_FROM_WIN32(ERROR_CANCELLED)
	nteBadAlgID           = 0x80090008
	nteBadKeyset          = 0x80090016
	nteBufferTooSmall     = 0x80090028
	nteUserCancelled      = 0x80090036
	nteNoKey              = 0x8009000D
	nteNotFound           = 0x80090011
	nteSilentContext      = 0x80090022
	scardECancelled       = 0x80100002
	scardENoSmartcard     = 0x8010000C
	scardENoReaders       = 0x8010002E
	scardEReaderUnavail   = 0x80100017
	scardWRemovedCard     = 0x80100069
	scardWWrongCHV        = 0x8010006B
	scardWCHVBlocked      = 0x8010006C
	scardWCancelledByUser = 0x8010006E
	scardWCardNotAuth     = 0x8010006F
	errorInvalidPassword  = 86 // ERROR_INVALID_PASSWORD
	hrWrongPasswordWin32  = 0x80070056
	errorAccountLocked    = 0x80070775
)

// cryptKeyProvInfo mirrors CRYPT_KEY_PROV_INFO.
type cryptKeyProvInfo struct {
	ContainerName  *uint16
	ProvName       *uint16
	ProvType       uint32
	Flags          uint32
	ProvParamCount uint32
	ProvParam      uintptr
	KeySpec        uint32
}

// bcryptPKCS1PaddingInfo mirrors BCRYPT_PKCS1_PADDING_INFO.
type bcryptPKCS1PaddingInfo struct {
	AlgID *uint16
}

// WindowsStore implements Store on top of CurrentUser\MY.
type WindowsStore struct {
	mu sync.Mutex // one private-key operation (one PIN dialog) at a time
}

func newWindowsStore() *WindowsStore { return &WindowsStore{} }

func openMyStore() (windows.Handle, error) {
	name, _ := windows.UTF16PtrFromString("MY")
	return windows.CertOpenStore(
		windows.CERT_STORE_PROV_SYSTEM_W, 0, 0,
		windows.CERT_SYSTEM_STORE_CURRENT_USER|windows.CERT_STORE_OPEN_EXISTING_FLAG|windows.CERT_STORE_READONLY_FLAG,
		uintptr(unsafe.Pointer(name)))
}

func certDER(ctx *windows.CertContext) []byte {
	src := unsafe.Slice(ctx.EncodedCert, ctx.Length)
	out := make([]byte, len(src))
	copy(out, src)
	return out
}

// keyProvInfo reads CERT_KEY_PROV_INFO_PROP_ID without touching the key.
// ok=false means the certificate has no associated private key.
func keyProvInfo(ctx *windows.CertContext) (info *cryptKeyProvInfo, buf []uint64, ok bool) {
	var size uint32
	r, _, _ := procCertGetCertificateContextProperty.Call(
		uintptr(unsafe.Pointer(ctx)), certKeyProvInfoPropID, 0, uintptr(unsafe.Pointer(&size)))
	if r == 0 || size == 0 {
		return nil, nil, false
	}
	// []uint64 guarantees 8-byte alignment for the struct at the start.
	buf = make([]uint64, (size+7)/8)
	r, _, _ = procCertGetCertificateContextProperty.Call(
		uintptr(unsafe.Pointer(ctx)), certKeyProvInfoPropID,
		uintptr(unsafe.Pointer(&buf[0])), uintptr(unsafe.Pointer(&size)))
	if r == 0 {
		return nil, nil, false
	}
	return (*cryptKeyProvInfo)(unsafe.Pointer(&buf[0])), buf, true
}

// chainOf returns the DER of the issuing certificates (excluding the leaf
// and self-signed roots), using only locally cached data (no network).
func chainOf(ctx *windows.CertContext) [][]byte {
	const certChainCacheOnlyURLRetrieval = 0x00000004
	para := windows.CertChainPara{}
	para.Size = uint32(unsafe.Sizeof(para))
	var chainCtx *windows.CertChainContext
	if err := windows.CertGetCertificateChain(0, ctx, nil, 0, &para, certChainCacheOnlyURLRetrieval, 0, &chainCtx); err != nil || chainCtx == nil {
		return nil
	}
	defer windows.CertFreeCertificateChain(chainCtx)
	if chainCtx.ChainCount == 0 || chainCtx.Chains == nil {
		return nil
	}
	simple := *chainCtx.Chains // first simple chain
	if simple == nil || simple.NumElements == 0 {
		return nil
	}
	elems := unsafe.Slice(simple.Elements, simple.NumElements)
	var out [][]byte
	for i := 1; i < len(elems); i++ {
		e := elems[i]
		if e == nil || e.CertContext == nil {
			continue
		}
		der := certDER(e.CertContext)
		if m, err := parseCertMeta(der); err == nil && m.SelfSigned {
			continue
		}
		out = append(out, der)
	}
	return out
}

// List implements Store.
func (s *WindowsStore) List() ([]CertInfo, error) {
	store, err := openMyStore()
	if err != nil {
		return nil, fmt.Errorf("CertOpenStore: %w", err)
	}
	defer windows.CertCloseStore(store, 0)

	now := time.Now()
	certs := []CertInfo{}
	var prev *windows.CertContext
	for {
		ctx, err := windows.CertEnumCertificatesInStore(store, prev)
		if err != nil || ctx == nil {
			break // CRYPT_E_NOT_FOUND: end of store
		}
		prev = ctx
		if _, _, ok := keyProvInfo(ctx); !ok {
			continue
		}
		der := certDER(ctx)
		info, _, err := buildCertInfo(der, chainOf(ctx), now)
		if err != nil {
			logf("bỏ qua chứng thư %s: %v", thumbprint(der), err)
			continue
		}
		certs = append(certs, info)
	}
	return certs, nil
}

// findCert returns a duplicated context for the thumbprint (caller frees).
func findCert(store windows.Handle, id string) *windows.CertContext {
	var prev *windows.CertContext
	for {
		ctx, err := windows.CertEnumCertificatesInStore(store, prev)
		if err != nil || ctx == nil {
			return nil
		}
		prev = ctx
		if thumbprint(certDER(ctx)) == id {
			// Duplicate so the context survives the enumeration; the
			// enumeration context itself is freed by the next call or
			// must be freed by us when we stop early.
			dup := windows.CertDuplicateCertificateContext(ctx)
			windows.CertFreeCertificateContext(ctx)
			return dup
		}
	}
}

// Sign implements Store.
func (s *WindowsStore) Sign(id string, digest []byte) ([]byte, string, error) {
	if len(digest) != 32 {
		return nil, "", &AgentError{Code: codeBadRequest, Message: "Giá trị băm phải dài 32 byte."}
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	store, err := openMyStore()
	if err != nil {
		return nil, "", &AgentError{Code: codeSignFailed, Message: msgSignFailed, Err: err}
	}
	defer windows.CertCloseStore(store, 0)

	ctx := findCert(store, normalizeThumbprint(id))
	if ctx == nil {
		return nil, "", ErrCertNotFound
	}
	defer windows.CertFreeCertificateContext(ctx)

	meta, err := parseCertMeta(certDER(ctx))
	if err != nil {
		return nil, "", &AgentError{Code: codeUnsupported, Message: msgUnsupportedKey, Err: err}
	}
	provInfo, provBuf, ok := keyProvInfo(ctx)
	if !ok {
		return nil, "", ErrCertNotFound
	}

	// Not silent: the token driver must be able to show its PIN dialog.
	var (
		h          windows.Handle
		keySpec    uint32
		callerFree bool
	)
	if err := windows.CryptAcquireCertificatePrivateKey(ctx, windows.CRYPT_ACQUIRE_ALLOW_NCRYPT_KEY_FLAG, nil, &h, &keySpec, &callerFree); err != nil {
		return nil, "", mapWinError("CryptAcquireCertificatePrivateKey", err)
	}

	var sig []byte
	if keySpec == windows.CERT_NCRYPT_KEY_SPEC {
		if callerFree {
			defer procNCryptFreeObject.Call(uintptr(h))
		}
		sig, err = ncryptSign(h, meta, digest)
	} else {
		if callerFree {
			defer windows.CryptReleaseContext(h, 0)
		}
		sig, err = capiSign(h, keySpec, meta, digest, provInfo)
	}
	runtime.KeepAlive(provBuf) // provInfo points into this buffer
	if err != nil {
		return nil, "", err
	}
	return sig, meta.KeyType, nil
}

func foregroundWindow() uintptr {
	r, _, _ := procGetForegroundWindow.Call()
	return r
}

// ncryptSign signs with a CNG (KSP) key.
func ncryptSign(key windows.Handle, meta *certMeta, digest []byte) ([]byte, error) {
	// Parent the KSP's PIN dialog to the current foreground window
	// (usually the browser) so it does not appear behind it. Best effort.
	if hwnd := foregroundWindow(); hwnd != 0 {
		prop, _ := windows.UTF16PtrFromString("HWND Handle") // NCRYPT_WINDOW_HANDLE_PROPERTY
		procNCryptSetProperty.Call(uintptr(key), uintptr(unsafe.Pointer(prop)),
			uintptr(unsafe.Pointer(&hwnd)), unsafe.Sizeof(hwnd), 0)
	}

	var padding unsafe.Pointer
	var flags uintptr
	var pad bcryptPKCS1PaddingInfo
	if meta.KeyType == "rsa" {
		pad.AlgID, _ = windows.UTF16PtrFromString("SHA256") // BCRYPT_SHA256_ALGORITHM
		padding = unsafe.Pointer(&pad)
		flags = bcryptPadPKCS1
	}
	call := func(out []byte) (uint32, uint32) {
		var outPtr uintptr
		if len(out) > 0 {
			outPtr = uintptr(unsafe.Pointer(&out[0]))
		}
		var result uint32
		r, _, _ := procNCryptSignHash.Call(uintptr(key), uintptr(padding),
			uintptr(unsafe.Pointer(&digest[0])), uintptr(len(digest)),
			outPtr, uintptr(len(out)), uintptr(unsafe.Pointer(&result)), flags)
		return uint32(r), result
	}
	// Single call with a buffer of the expected size (avoids a second PIN
	// prompt with drivers that authenticate on the size query); fall back
	// to the documented two-call pattern if the buffer is too small.
	buf := make([]byte, meta.SigSize)
	status, n := call(buf)
	if status == nteBufferTooSmall || (status == 0 && n > uint32(len(buf))) {
		_, need := call(nil)
		buf = make([]byte, need)
		status, n = call(buf)
	}
	if status != 0 {
		return nil, mapWinError("NCryptSignHash", syscall.Errno(status))
	}
	sig := buf[:n]
	if meta.KeyType == "ec" {
		return ecdsaRawToDER(sig)
	}
	return sig, nil
}

// capiSign signs with a legacy CryptoAPI (CSP) key. CryptoAPI only does
// RSA here (no ECDSA in legacy CSPs).
func capiSign(prov windows.Handle, keySpec uint32, meta *certMeta, digest []byte, info *cryptKeyProvInfo) ([]byte, error) {
	if meta.KeyType != "rsa" {
		return nil, &AgentError{Code: codeUnsupported, Message: msgUnsupportedKey}
	}
	if hwnd := foregroundWindow(); hwnd != 0 {
		procCryptSetProvParam.Call(uintptr(prov), ppClientHWND, uintptr(unsafe.Pointer(&hwnd)), 0)
	}
	sig, err := capiSignWith(prov, keySpec, meta, digest)
	if err == nil || errCode(err) != nteBadAlgID {
		return sig, err
	}
	// Legacy PROV_RSA_FULL providers (e.g. "Microsoft Base Cryptographic
	// Provider") do not know CALG_SHA_256. Best effort: re-open the same
	// key container with a PROV_RSA_AES provider, first with the same
	// provider name, then with the default AES provider. This works for
	// keys stored by Microsoft software CSPs; a vendor token CSP normally
	// supports SHA-256 itself.
	logf("CSP không hỗ trợ SHA-256, thử lại với PROV_RSA_AES")
	if info == nil || info.ContainerName == nil {
		return nil, mapWinError("CryptCreateHash", err)
	}
	for _, provName := range []*uint16{info.ProvName, nil} {
		var h2 windows.Handle
		if e := windows.CryptAcquireContext(&h2, info.ContainerName, provName, windows.PROV_RSA_AES, info.Flags&windows.CRYPT_MACHINE_KEYSET); e != nil {
			continue
		}
		sig, e := capiSignWith(h2, keySpec, meta, digest)
		windows.CryptReleaseContext(h2, 0)
		if e == nil {
			return sig, nil
		}
		err = e
		if errCode(e) != nteBadAlgID {
			break
		}
	}
	return nil, mapWinError("CryptSignHash", err)
}

func capiSignWith(prov windows.Handle, keySpec uint32, meta *certMeta, digest []byte) ([]byte, error) {
	var hash windows.Handle
	r, _, e := procCryptCreateHash.Call(uintptr(prov), calgSHA256, 0, 0, uintptr(unsafe.Pointer(&hash)))
	if r == 0 {
		return nil, mapWinError("CryptCreateHash", e)
	}
	defer procCryptDestroyHash.Call(uintptr(hash))
	r, _, e = procCryptSetHashParam.Call(uintptr(hash), hpHashVal, uintptr(unsafe.Pointer(&digest[0])), 0)
	if r == 0 {
		return nil, mapWinError("CryptSetHashParam", e)
	}
	call := func(out []byte, size *uint32) (bool, error) {
		var outPtr uintptr
		if len(out) > 0 {
			outPtr = uintptr(unsafe.Pointer(&out[0]))
		}
		r, _, e := procCryptSignHashW.Call(uintptr(hash), uintptr(keySpec), 0, 0, outPtr, uintptr(unsafe.Pointer(size)))
		return r != 0, e
	}
	size := uint32(meta.SigSize)
	buf := make([]byte, size)
	ok, e := call(buf, &size)
	if !ok && errCode(e) == errMoreData {
		buf = make([]byte, size)
		ok, e = call(buf, &size)
	}
	if !ok {
		return nil, mapWinError("CryptSignHash", e)
	}
	// CryptoAPI returns the signature little-endian.
	return reverseBytes(buf[:size]), nil
}

// errCode extracts the numeric Win32/HRESULT code from an error.
func errCode(err error) uint32 {
	var errno syscall.Errno
	if errors.As(err, &errno) {
		return uint32(errno)
	}
	return 0
}

// mapWinError converts Windows / smart card errors into protocol errors
// with a Vietnamese message.
func mapWinError(op string, err error) error {
	code := errCode(err)
	wrapped := fmt.Errorf("%s: %w (0x%08X)", op, err, code)
	switch code {
	case errCancelled, hrCancelled, nteUserCancelled, scardECancelled, scardWCancelledByUser:
		return &AgentError{Code: codeUserCancelled, Message: msgUserCancelled, Err: wrapped}
	case scardWWrongCHV, errorInvalidPassword, hrWrongPasswordWin32, scardWCardNotAuth:
		return &AgentError{Code: codeSignFailed, Message: "Mã PIN không đúng. Vui lòng thử lại (lưu ý: nhập sai nhiều lần có thể khóa USB token).", Err: wrapped}
	case scardWCHVBlocked, errorAccountLocked:
		return &AgentError{Code: codeSignFailed, Message: "USB token đã bị khóa do nhập sai mã PIN quá số lần cho phép. Vui lòng liên hệ nhà cung cấp chữ ký số.", Err: wrapped}
	case scardENoSmartcard, scardENoReaders, scardEReaderUnavail, scardWRemovedCard, nteBadKeyset, nteNoKey, nteNotFound:
		return &AgentError{Code: codeSignFailed, Message: "Không truy cập được USB token. Hãy cắm USB token vào máy, kiểm tra phần mềm (driver) của nhà cung cấp chữ ký số rồi thử lại.", Err: wrapped}
	case nteSilentContext:
		return &AgentError{Code: codeSignFailed, Message: "Không hiển thị được hộp thoại nhập mã PIN của USB token.", Err: wrapped}
	case nteBadAlgID:
		return &AgentError{Code: codeUnsupported, Message: "Phần mềm của USB token không hỗ trợ ký SHA-256.", Err: wrapped}
	}
	return &AgentError{Code: codeSignFailed, Message: msgSignFailed + " (mã lỗi 0x" + fmt.Sprintf("%08X", code) + ")", Err: wrapped}
}
