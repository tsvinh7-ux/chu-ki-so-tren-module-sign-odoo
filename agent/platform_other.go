//go:build !windows

package main

import "os"

type platformOptions struct{}

func platformStart(args []string) (platformOptions, bool, int) {
	for _, a := range args {
		switch a {
		case "-version", "--version", "/version":
			os.Stdout.WriteString(agentName + " " + version + "\n")
			return platformOptions{}, true, 0
		}
	}
	return platformOptions{}, false, 0
}

func platformAlreadyRunning(platformOptions) {}

// platformExtraOrigins: DMAG_AGENT_ALLOWED_ORIGINS (comma separated) extends
// the built-in list.
func platformExtraOrigins() []string {
	return splitOrigins(os.Getenv("DMAG_AGENT_ALLOWED_ORIGINS"))
}

func platformStore() (Store, string, error) {
	s, err := NewPEMStoreFromEnv()
	if s == nil {
		s = &PEMStore{}
	}
	return s, "pem", err
}

// autoPrompter never asks: unknown origins are rejected and signatures are
// approved unless DMAG_AGENT_AUTO_REJECT=1.
type autoPrompter struct{}

func (autoPrompter) Interactive() bool     { return false }
func (autoPrompter) AskOrigin(string) bool { return false }
func (autoPrompter) ConfirmSign(_, _, _ string) bool {
	return os.Getenv("DMAG_AGENT_AUTO_REJECT") != "1"
}

func platformPrompter() Prompter { return autoPrompter{} }
