package main

import (
	"net"
	"net/netip"
	"tailscale.com/ipn/ipnstate"
	"testing"
	"time"
)

func TestConfigLimits(t *testing.T) {
	c := config{Mode: "employee", Dir: "private-state", Hostname: "employee-1", Target: "nexsearch-host", ControlURL: "https://controlplane.tailscale.com", Port: 8743}
	if err := c.validate(); err != nil {
		t.Fatal(err)
	}
	for _, value := range []string{"http://control.example.org", "https://user:secret@control.example.org", "https://control.example.org/?key=secret"} {
		bad := c
		bad.ControlURL = value
		if bad.validate() == nil {
			t.Fatal("unsafe control URL accepted")
		}
	}
}
func TestFindsOnlyNamedOnlinePeer(t *testing.T) {
	peers := map[string]*ipnstate.PeerStatus{"a": {HostName: "other", Online: true, TailscaleIPs: []netip.Addr{netip.MustParseAddr("100.64.0.2")}}, "b": {DNSName: "nexsearch-host.tail.example.", Online: true, TailscaleIPs: []netip.Addr{netip.MustParseAddr("100.64.0.3")}}}
	if got := peerAddress(peers, "nexsearch-host"); got != "100.64.0.3" {
		t.Fatal(got)
	}
	peers["b"].Online = false
	if peerAddress(peers, "nexsearch-host") != "" {
		t.Fatal("offline peer selected")
	}
}
func TestCopiesApplicationBytesWithoutTLSInterception(t *testing.T) {
	a, in := net.Pipe()
	out, b := net.Pipe()
	defer a.Close()
	defer b.Close()
	go copyDuplex(in, out)
	expected := []byte("TLS encrypted application bytes")
	go func() { _, _ = a.Write(expected); _ = a.Close() }()
	_ = b.SetReadDeadline(time.Now().Add(time.Second))
	got := make([]byte, len(expected))
	n, err := b.Read(got)
	if err != nil || string(got[:n]) != string(expected) {
		t.Fatal(n, err)
	}
}
