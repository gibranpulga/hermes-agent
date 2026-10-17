"""Freeze socket owners, rather than an arbitrary dashboard CLI process."""
from scripts.agentdesk.lifecycle import listening_pids


def test_http_fault_finds_ipv4_ipv6_and_shared_listener_owners(tmp_path):
    net = tmp_path / 'net'
    net.mkdir()
    header = 'sl local_address rem_address st queues timer retrnsmt uid timeout inode\n'
    def row(port, inode):
        return f'0: 00000000:{port:04X} 00000000:0000 0A 0:0 0:0 0 10000 0 {inode}\n'
    (net / 'tcp').write_text(header + row(9119, 123) + row(9120, 456), encoding='utf-8')
    (net / 'tcp6').write_text(header + row(9119, 789), encoding='utf-8')
    for pid, inode in [(1, 123), (11, 123), (12, 123), (13, 456), (14, 789)]:
        descriptors = tmp_path / str(pid) / 'fd'
        descriptors.mkdir(parents=True)
        (descriptors / '3').symlink_to(f'socket:[{inode}]')
    assert listening_pids(tmp_path, 9119) == {11, 12, 14}
    assert listening_pids(tmp_path, 9120) == {13}
    assert listening_pids(tmp_path, 9999) == set()
