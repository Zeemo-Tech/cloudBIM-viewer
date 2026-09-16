"""Surfaceless EGL pixel test; uses system EGL/GLES and Python's standard library."""
import ctypes as c
import json
import sys

E = c.CDLL('libEGL.so.1')
G = c.CDLL('libGLESv2.so.2')
I, U, P, F = c.c_int, c.c_uint, c.c_void_p, c.c_float


def fn(lib, name, result, args):
    function = getattr(lib, name)
    function.restype, function.argtypes = result, args
    return function


proc = fn(E, 'eglGetProcAddress', P, [c.c_char_p])(b'eglGetPlatformDisplayEXT')
display = c.CFUNCTYPE(P, U, P, P)(proc)(0x31DD, None, None)
major, minor = I(), I()
assert fn(E, 'eglInitialize', U, [P, P, P])(display, c.byref(major), c.byref(minor))
attrs = (I * 13)(0x3033, 1, 0x3040, 0x40, 0x3024, 8, 0x3023, 8, 0x3022, 8, 0x3021, 8, 0x3038)
config, count = P(), I()
assert fn(E, 'eglChooseConfig', U, [P, P, P, I, P])(display, attrs, c.byref(config), 1, c.byref(count)) and count.value
assert fn(E, 'eglBindAPI', U, [U])(0x30A0)
context = fn(E, 'eglCreateContext', P, [P, P, P, P])(display, config, None, (I * 3)(0x3098, 3, 0x3038))
surface = fn(E, 'eglCreatePbufferSurface', P, [P, P, P])(display, config, (I * 5)(0x3057, 8, 0x3056, 8, 0x3038))
assert fn(E, 'eglMakeCurrent', U, [P, P, P, P])(display, surface, surface, context)

data = json.load(sys.stdin)
program = fn(G, 'glCreateProgram', U, [])()
for kind, key in [(0x8B31, 'vertex'), (0x8B30, 'fragment')]:
    shader = fn(G, 'glCreateShader', U, [U])(kind)
    source = c.c_char_p(data[key].encode())
    fn(G, 'glShaderSource', None, [U, I, P, P])(shader, 1, c.byref(source), None)
    fn(G, 'glCompileShader', None, [U])(shader)
    ok, log = I(), c.create_string_buffer(8192)
    fn(G, 'glGetShaderiv', None, [U, U, P])(shader, 0x8B81, c.byref(ok))
    fn(G, 'glGetShaderInfoLog', None, [U, I, P, P])(shader, 8192, None, log)
    assert ok.value, log.value.decode()
    fn(G, 'glAttachShader', None, [U, U])(program, shader)
    fn(G, 'glDeleteShader', None, [U])(shader)
fn(G, 'glLinkProgram', None, [U])(program)
ok, log = I(), c.create_string_buffer(8192)
fn(G, 'glGetProgramiv', None, [U, U, P])(program, 0x8B82, c.byref(ok))
fn(G, 'glGetProgramInfoLog', None, [U, I, P, P])(program, 8192, None, log)
assert ok.value, log.value.decode()
fn(G, 'glUseProgram', None, [U])(program)

loc = lambda name: fn(G, 'glGetUniformLocation', I, [U, c.c_char_p])(program, name.encode())
uniform = fn(G, 'glUniform1f', None, [I, F])
for name, value in [('cameraNear', 0.01), ('cameraFar', 1000), ('edlRadius', 1)]:
    uniform(loc(name), value)
fn(G, 'glUniform2f', None, [I, F, F])(loc('resolution'), 8, 8)
for unit, name in enumerate(['tDiffuse', 'tDepth']):
    fn(G, 'glUniform1i', None, [I, I])(loc(name), unit)

vao, buffer = U(), U()
fn(G, 'glGenVertexArrays', None, [I, P])(1, c.byref(vao))
fn(G, 'glBindVertexArray', None, [U])(vao)
fn(G, 'glGenBuffers', None, [I, P])(1, c.byref(buffer))
fn(G, 'glBindBuffer', None, [U, U])(0x8892, buffer)
vertices = (F * 12)(-1, -1, 0, 1, -1, 0, -1, 1, 0, 1, 1, 0)
fn(G, 'glBufferData', None, [U, c.c_ssize_t, P, U])(0x8892, c.sizeof(vertices), vertices, 0x88E4)
position = fn(G, 'glGetAttribLocation', I, [U, c.c_char_p])(program, b'position')
fn(G, 'glEnableVertexAttribArray', None, [U])(position)
fn(G, 'glVertexAttribPointer', None, [U, I, U, U, I, P])(position, 3, 0x1406, 0, 0, None)
fn(G, 'glViewport', None, [I, I, I, I])(0, 0, 8, 8)

textures = (U * 2)()
fn(G, 'glGenTextures', None, [I, P])(2, textures)


def texture(unit, values, internal, format_, type_):
    fn(G, 'glActiveTexture', None, [U])(0x84C0 + unit)
    fn(G, 'glBindTexture', None, [U, U])(0x0DE1, textures[unit])
    for parameter, value in [(0x2801, 0x2600), (0x2800, 0x2600), (0x2802, 0x812F), (0x2803, 0x812F)]:
        fn(G, 'glTexParameteri', None, [U, U, I])(0x0DE1, parameter, value)
    fn(G, 'glTexImage2D', None, [U, I, I, I, I, I, U, U, P])(0x0DE1, 0, internal, 8, 8, 0, format_, type_, values)


def srgb(byte):
    linear = byte / 255
    return round(255 * (12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1 / 2.4) - 0.055))


checks = 0
for background, grid in [([1, 1, 4], [3, 12, 20]), ([210, 225, 240], [100, 120, 140])]:
    colors, depths = [], []
    for y in range(8):
        for x in range(8):
            point = 2 <= x <= 5 and 2 <= y <= 5
            colors.extend(([64, 64, 64] if point else grid if x % 2 else background) + [255])
            depths.append((0.2 if (x, y) == (3, 3) else 0.5) if point else 1.0)
    texture(0, (c.c_ubyte * len(colors))(*colors), 0x8058, 0x1908, 0x1401)
    texture(1, (F * 64)(*depths), 0x822E, 0x1903, 0x1406)
    for orthographic in [0, 1]:
        uniform(loc('orthographic'), orthographic)
        outputs = []
        for strength in [0, 1]:
            uniform(loc('edlStrength'), strength)
            fn(G, 'glDrawArrays', None, [U, I, I])(0x0005, 0, 4)
            pixels = (c.c_ubyte * 256)()
            fn(G, 'glReadPixels', None, [I, I, I, I, U, U, P])(0, 0, 8, 8, 0x1908, 0x1401, pixels)
            assert fn(G, 'glGetError', U, [])() == 0
            for index, depth in enumerate(depths):
                if depth == 1 or strength == 0:
                    for channel in range(3):
                        expected = srgb(colors[index * 4 + channel])
                        actual = pixels[index * 4 + channel]
                        assert abs(actual - expected) <= 1, f'EDL output lost display color: {actual=}, {expected=}, {depth=}, {strength=}'
            outputs.append(list(pixels))
            checks += 1
        assert outputs[1][(3 * 8 + 3) * 4] < outputs[0][(3 * 8 + 3) * 4], 'EDL must still enhance point depth boundaries'

fn(E, 'eglMakeCurrent', U, [P, P, P, P])(display, None, None, None)
fn(E, 'eglDestroySurface', U, [P, P])(display, surface)
fn(E, 'eglDestroyContext', U, [P, P])(display, context)
fn(E, 'eglTerminate', U, [P])(display)
print(f'PASS: {checks} GPU frames preserve dark/light grid and background colors, and EDL still enhances point depth boundaries.')
