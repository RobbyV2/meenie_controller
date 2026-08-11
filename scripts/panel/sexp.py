import re

TOK = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+')


def parse(text):
    stack = [[]]
    for m in TOK.finditer(text):
        t = m.group(0)
        if t == '(':
            n = []
            stack[-1].append(n)
            stack.append(n)
        elif t == ')':
            stack.pop()
        else:
            stack[-1].append(t)
    return stack[0][0]


def dump(node, ind=0):
    def w(n, d):
        if isinstance(n, str):
            return n
        if all(isinstance(c, str) for c in n):
            return '(' + ' '.join(n) + ')'
        parts = []
        head = []
        for c in n:
            if isinstance(c, str) and not parts:
                head.append(c)
            else:
                parts.append('\n' + '\t' * (d + 1) + w(c, d + 1) if isinstance(c, list)
                             else ' ' + c)
        return '(' + ' '.join(head) + ''.join(parts) + '\n' + '\t' * d + ')'
    return w(node, ind)


def tag(n):
    return n[0] if isinstance(n, list) and n and isinstance(n[0], str) else None


def kids(n, name):
    return [c for c in n if isinstance(c, list) and tag(c) == name]


def kid(n, name):
    k = kids(n, name)
    return k[0] if k else None


def unq(s):
    return s[1:-1] if len(s) > 1 and s[0] == '"' else s
