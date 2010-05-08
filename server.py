import re, struct, socket, select, subprocess
if not globals().get('skip_imports'):
    import ssnet, helpers
    from ssnet import SockWrapper, Handler, Proxy, Mux, MuxWrapper
    from helpers import *


def _ipmatch(ipstr):
    if ipstr == 'default':
        ipstr = '0.0.0.0/0'
    m = re.match(r'^(\d+(\.\d+(\.\d+(\.\d+)?)?)?)(?:/(\d+))?$', ipstr)
    if m:
        g = m.groups()
        ips = g[0]
        width = int(g[4] or 32)
        if g[1] == None:
            ips += '.0.0.0'
            width = min(width, 8)
        elif g[2] == None:
            ips += '.0.0'
            width = min(width, 16)
        elif g[3] == None:
            ips += '.0'
            width = min(width, 24)
        return (struct.unpack('!I', socket.inet_aton(ips))[0], width)


def _ipstr(ip, width):
    if width >= 32:
        return ip
    else:
        return "%s/%d" % (ip, width)


def _maskbits(netmask):
    if not netmask:
        return 32
    for i in range(32):
        if netmask[0] & (1<<i):
            return 32-i
    return 0


def _list_routes():
    argv = ['netstat', '-rn']
    p = subprocess.Popen(argv, stdout=subprocess.PIPE)
    routes = []
    for line in p.stdout:
        cols = re.split(r'\s+', line)
        ipw = _ipmatch(cols[0])
        if not ipw:
            continue  # some lines won't be parseable; never mind
        maskw = _ipmatch(cols[2])  # linux only
        mask = _maskbits(maskw)   # returns 32 if maskw is null
        width = min(ipw[1], mask)
        ip = ipw[0] & (((1<<width)-1) << (32-width))
        routes.append((socket.inet_ntoa(struct.pack('!I', ip)), width))
    rv = p.wait()
    if rv != 0:
        raise Fatal('%r returned %d' % (argv, rv))
    return routes


def list_routes():
    for (ip,width) in _list_routes():
        if not ip.startswith('0.') and not ip.startswith('127.'):
            yield (ip,width)
        


def main():
    if helpers.verbose >= 1:
        helpers.logprefix = ' s: '
    else:
        helpers.logprefix = 'server: '

    routes = list(list_routes())
    debug1('available routes:\n')
    for r in routes:
        debug1('  %s/%d\n' % r)
        
    # synchronization header
    sys.stdout.write('SSHUTTLE0001')
    sys.stdout.flush()

    handlers = []
    mux = Mux(socket.fromfd(sys.stdin.fileno(),
                            socket.AF_INET, socket.SOCK_STREAM),
              socket.fromfd(sys.stdout.fileno(),
                            socket.AF_INET, socket.SOCK_STREAM))
    handlers.append(mux)
    routepkt = ''.join('%s,%d\n' % r
                       for r in routes)
    mux.send(0, ssnet.CMD_ROUTES, routepkt)

    def new_channel(channel, data):
        (dstip,dstport) = data.split(',', 1)
        dstport = int(dstport)
        outwrap = ssnet.connect_dst(dstip,dstport)
        handlers.append(Proxy(MuxWrapper(mux, channel), outwrap))
    
    mux.new_channel = new_channel

    while mux.ok:
        r = set()
        w = set()
        x = set()
        handlers = filter(lambda s: s.ok, handlers)
        for s in handlers:
            s.pre_select(r,w,x)
        debug2('Waiting: %d[%d,%d,%d] (fullness=%d/%d)...\n' 
               % (len(handlers), len(r), len(w), len(x),
                  mux.fullness, mux.too_full))
        (r,w,x) = select.select(r,w,x)
        #log('r=%r w=%r x=%r\n' % (r,w,x))
        ready = set(r) | set(w) | set(x)
        for s in handlers:
            #debug2('check: %r: %r\n' % (s, s.socks & ready))
            if s.socks & ready:
                s.callback()
        mux.check_fullness()
        mux.callback()
