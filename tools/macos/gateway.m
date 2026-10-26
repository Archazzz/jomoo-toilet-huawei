// Python runs off the main thread; Cocoa owns the main run loop for OS consent.
#include <Python.h>
#include <pthread.h>
#import <AppKit/AppKit.h>
#import <CoreBluetooth/CoreBluetooth.h>

static void *run_gateway(void *unused) {
    @autoreleasepool {
        char *args[] = {JOMOO_PYTHON, "-m", "jomoo_gateway.server", "--config", JOMOO_CONFIG};
        PyConfig cfg;
        PyConfig_InitPythonConfig(&cfg);
        cfg.install_signal_handlers = 0;
        PyStatus status = PyConfig_SetBytesString(&cfg, &cfg.program_name, args[0]);
        if (PyStatus_Exception(status)) Py_ExitStatusException(status);
        status = PyConfig_SetBytesArgv(&cfg, 5, args);
        if (PyStatus_Exception(status)) Py_ExitStatusException(status);
        status = Py_InitializeFromConfig(&cfg);
        PyConfig_Clear(&cfg);
        if (PyStatus_Exception(status)) Py_ExitStatusException(status);
        exit(Py_RunMain());
    }
    return NULL;
}

@interface GatewayApp : NSObject <NSApplicationDelegate, CBCentralManagerDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) NSTextField *label;
@property(nonatomic, strong) CBCentralManager *manager;
@property(nonatomic) BOOL started;
@end

@implementation GatewayApp
- (void)startGateway {
    if (self.started) return;
    self.started = YES;
    pthread_t worker;
    if (pthread_create(&worker, NULL, run_gateway, NULL)) exit(1);
    [self.window orderOut:nil];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    if ([CBManager authorization] == CBManagerAuthorizationAllowedAlways) {
        [self startGateway];
        return;
    }
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 440, 180)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"九牧蓝牙网关";
    self.label = [NSTextField wrappingLabelWithString:
        @"需要蓝牙权限连接你配置的九牧马桶。请点击下方按钮，并在 macOS 提示中允许访问。"];
    self.label.frame = NSMakeRect(24, 80, 390, 64);
    [self.window.contentView addSubview:self.label];
    NSButton *button = [NSButton buttonWithTitle:@"请求蓝牙访问" target:self action:@selector(requestAccess:)];
    button.frame = NSMakeRect(140, 24, 160, 36);
    [self.window.contentView addSubview:button];
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}
- (void)requestAccess:(id)sender {
    self.manager = [[CBCentralManager alloc] initWithDelegate:self queue:dispatch_get_main_queue()];
    self.label.stringValue = @"已请求蓝牙访问，请在系统提示中允许。若此前拒绝，请在系统设置的蓝牙隐私列表中启用九牧蓝牙网关。";
}
- (void)centralManagerDidUpdateState:(CBCentralManager *)central {
    if (central.state == CBManagerStatePoweredOn) [self startGateway];
}
@end

int main(void) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        GatewayApp *delegate = [GatewayApp new];
        NSApp.delegate = delegate;
        [NSApp run];
    }
    return 0;
}
