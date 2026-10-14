package main

import (
	"io"
	"log"
	"os"
	"sync"
)

var logger = log.New(os.Stderr, "", log.LstdFlags)

func logf(format string, args ...any) {
	logger.Printf(format, args...)
}

// rotatingFile is an append-only log file that is moved to <path>.1 once it
// grows beyond max bytes (so at most ~2*max bytes are kept on disk).
type rotatingFile struct {
	mu   sync.Mutex
	path string
	max  int64
	f    *os.File
	size int64
}

func openRotatingFile(path string, max int64) (*rotatingFile, error) {
	r := &rotatingFile{path: path, max: max}
	if err := r.open(); err != nil {
		return nil, err
	}
	if r.size > max {
		if err := r.rotate(); err != nil {
			return nil, err
		}
	}
	return r, nil
}

func (r *rotatingFile) open() error {
	f, err := os.OpenFile(r.path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	st, err := f.Stat()
	if err != nil {
		f.Close()
		return err
	}
	r.f, r.size = f, st.Size()
	return nil
}

func (r *rotatingFile) rotate() error {
	r.f.Close()
	_ = os.Remove(r.path + ".1")
	if err := os.Rename(r.path, r.path+".1"); err != nil {
		// Fall back to truncation.
		_ = os.Truncate(r.path, 0)
	}
	return r.open()
}

func (r *rotatingFile) Write(p []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.size+int64(len(p)) > r.max {
		if err := r.rotate(); err != nil {
			return 0, err
		}
	}
	n, err := r.f.Write(p)
	r.size += int64(n)
	return n, err
}

func (r *rotatingFile) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.f.Close()
}

var _ io.WriteCloser = (*rotatingFile)(nil)
