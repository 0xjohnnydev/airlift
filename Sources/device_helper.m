#import <CoreFoundation/CoreFoundation.h>
#import <Foundation/Foundation.h>
#include <sys/socket.h>
#include <unistd.h>

#import "airlift_target.h"

typedef const void *AMDeviceRef;
typedef const void *AMDeviceNotificationRef;
typedef void *AMDServiceConnectionRef;
typedef void *AFCConnectionRef;
typedef void *AFCKeyValueRef;
typedef void *AFCFileRef;
typedef void *AFCDirectoryRef;

typedef struct {
    AMDeviceRef device;
    unsigned int message;
} AMDeviceNotificationCallbackInfo;

extern int AMDeviceNotificationSubscribeWithOptions(
    void (*callback)(AMDeviceNotificationCallbackInfo *, void *),
    int unused,
    unsigned int connectionType,
    void *context,
    AMDeviceNotificationRef *subscription,
    CFDictionaryRef options);
extern int AMDeviceNotificationUnsubscribe(AMDeviceNotificationRef subscription);
extern CFStringRef AMDeviceCopyDeviceIdentifier(AMDeviceRef device);
extern CFTypeRef AMDeviceCopyValue(AMDeviceRef device,
                                   CFStringRef domain,
                                   CFStringRef key);
extern int AMDeviceConnect(AMDeviceRef device);
extern int AMDeviceDisconnect(AMDeviceRef device);
extern int AMDeviceIsPaired(AMDeviceRef device);
extern int AMDeviceValidatePairing(AMDeviceRef device);
extern int AMDeviceStartSession(AMDeviceRef device);
extern int AMDeviceStopSession(AMDeviceRef device);
extern int AMDeviceSecureStartService(AMDeviceRef device,
                                      CFStringRef serviceName,
                                      CFDictionaryRef options,
                                      AMDServiceConnectionRef *connection);
extern int AMDServiceConnectionGetSocket(AMDServiceConnectionRef connection);
extern void *AMDServiceConnectionGetSecureIOContext(
    AMDServiceConnectionRef connection);
extern int AMDServiceConnectionInvalidate(AMDServiceConnectionRef connection);
extern int AMDServiceConnectionSend(AMDServiceConnectionRef connection,
                                    const void *bytes,
                                    size_t length);
extern int AMDServiceConnectionSendMessage(AMDServiceConnectionRef connection,
                                           CFTypeRef message,
                                           CFPropertyListFormat format);
extern int AMDServiceConnectionReceiveMessage(AMDServiceConnectionRef connection,
                                              CFTypeRef *message,
                                              CFPropertyListFormat *format);

extern int AFCConnectionOpen(int socket,
                             unsigned int ioTimeout,
                             AFCConnectionRef *connection);
extern int AFCConnectionClose(AFCConnectionRef connection);
extern int AFCConnectionSetSecureContext(AFCConnectionRef connection,
                                         void *secureContext);
extern int AFCConnectionSetDisposeSecureContextOnInvalidate(
    AFCConnectionRef connection,
    int dispose);
extern int AFCConnectionSetIOTimeout(AFCConnectionRef connection,
                                     unsigned int timeout);
extern int AFCFileInfoOpen(AFCConnectionRef connection,
                           const char *path,
                           AFCKeyValueRef *dictionary);
extern int AFCKeyValueRead(AFCKeyValueRef dictionary, char **key, char **value);
extern int AFCKeyValueClose(AFCKeyValueRef dictionary);
extern int AFCFileRefOpen(AFCConnectionRef connection,
                          const char *path,
                          unsigned long long mode,
                          AFCFileRef *file);
extern int AFCFileRefRead(AFCConnectionRef connection,
                          AFCFileRef file,
                          void *bytes,
                          long *length);
extern int AFCFileRefWrite(AFCConnectionRef connection,
                           AFCFileRef file,
                           const void *bytes,
                           long length);
extern int AFCFileRefClose(AFCConnectionRef connection, AFCFileRef file);
extern int AFCDirectoryOpen(AFCConnectionRef connection,
                            const char *path,
                            AFCDirectoryRef *directory);
extern int AFCDirectoryRead(AFCConnectionRef connection,
                            AFCDirectoryRef directory,
                            char **entry);
extern int AFCDirectoryClose(AFCConnectionRef connection,
                             AFCDirectoryRef directory);
extern int AFCDirectoryCreate(AFCConnectionRef connection, const char *path);
extern int AFCRemovePath(AFCConnectionRef connection, const char *path);

static const char *TrackedBooksFiles[] = {
    "Books/Books.plist",
    "Books/Sync/Books.plist",
    "Books/Sync/Upload.plist",
    "Books/Sync/Database/OutstandingAssets_4.sqlite",
    "Books/Sync/Database/OutstandingAssets_4.sqlite-shm",
    "Books/Sync/Database/OutstandingAssets_4.sqlite-wal",
};

static CFStringRef TargetIdentifier;
static AMDeviceRef TargetDevice;

typedef struct {
    AMDeviceRef device;
    BOOL connected;
    BOOL sessionStarted;
    AMDServiceConnectionRef afcService;
    AFCConnectionRef afc;
    int subscribeStatus;
    int connectStatus;
    int validateStatus;
    int sessionStatus;
    int serviceStatus;
    int afcStatus;
} DeviceSession;

static void DeviceCallback(AMDeviceNotificationCallbackInfo *info,
                           void *context) {
    (void)context;
    if (!info || !info->device || info->message != 1 || TargetDevice) return;
    CFStringRef identifier = AMDeviceCopyDeviceIdentifier(info->device);
    BOOL matches = identifier && CFEqual(identifier, TargetIdentifier);
    if (identifier) CFRelease(identifier);
    if (!matches) return;
    TargetDevice = CFRetain(info->device);
    CFRunLoopStop(CFRunLoopGetMain());
}

static int FindTarget(void) {
    NSDictionary *options = @{
        @"NotificationOptionSearchForPairedDevices": @YES,
        @"NotificationOptionSearchForPairedDevicesViaDirectConnectionsOnly": @NO,
        @"NotificationOptionSearchForWiFiPairableDevices": @NO,
        @"NotificationOptionEnableRemoteXPC": @YES,
        @"NotificationOptionEnableUSBMux": @YES,
    };
    AMDeviceNotificationRef subscription = NULL;
    int status = AMDeviceNotificationSubscribeWithOptions(
        DeviceCallback,
        0,
        0,
        NULL,
        &subscription,
        (__bridge CFDictionaryRef)options);
    if (status == 0)
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 30.0, false);
    if (subscription) AMDeviceNotificationUnsubscribe(subscription);
    return status;
}

static void OpenSession(DeviceSession *session) {
    memset(session, 0, sizeof(*session));
    session->connectStatus = session->validateStatus = -1;
    session->sessionStatus = session->serviceStatus = session->afcStatus = -1;
    session->subscribeStatus = FindTarget();
    session->device = TargetDevice;
    if (!session->device) return;

    session->connectStatus = AMDeviceConnect(session->device);
    session->connected = session->connectStatus == 0;
    if (!session->connected || !AMDeviceIsPaired(session->device)) return;
    session->validateStatus = AMDeviceValidatePairing(session->device);
    if (session->validateStatus != 0) return;
    session->sessionStatus = AMDeviceStartSession(session->device);
    session->sessionStarted = session->sessionStatus == 0;
    if (!session->sessionStarted) return;
    session->serviceStatus = AMDeviceSecureStartService(
        session->device,
        CFSTR("com.apple.afc"),
        NULL,
        &session->afcService);
    if (session->serviceStatus != 0 || !session->afcService) return;
    session->afcStatus = AFCConnectionOpen(
        AMDServiceConnectionGetSocket(session->afcService),
        0,
        &session->afc);
    void *secureContext =
        AMDServiceConnectionGetSecureIOContext(session->afcService);
    if (session->afcStatus == 0 && session->afc && secureContext) {
        AFCConnectionSetSecureContext(session->afc, secureContext);
        AFCConnectionSetDisposeSecureContextOnInvalidate(session->afc, 0);
        AFCConnectionSetIOTimeout(session->afc, 30);
    }
}

static void CloseSession(DeviceSession *session) {
    if (session->afc) AFCConnectionClose(session->afc);
    if (session->afcService)
        AMDServiceConnectionInvalidate(session->afcService);
    if (session->sessionStarted) AMDeviceStopSession(session->device);
    if (session->connected) AMDeviceDisconnect(session->device);
    if (session->device) CFRelease(session->device);
    TargetDevice = NULL;
}

static void PrintJSON(NSDictionary *object) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:object
                                                   options:0
                                                     error:nil];
    if (!data) return;
    fwrite(data.bytes, 1, data.length, stdout);
    fwrite("\n", 1, 1, stdout);
}

static BOOL AFCExists(AFCConnectionRef afc, NSString *path) {
    AFCKeyValueRef info = NULL;
    int status = AFCFileInfoOpen(afc, path.fileSystemRepresentation, &info);
    if (info) AFCKeyValueClose(info);
    return status == 0;
}

static long long AFCFileSize(AFCConnectionRef afc, NSString *path) {
    AFCKeyValueRef info = NULL;
    if (AFCFileInfoOpen(afc, path.fileSystemRepresentation, &info) != 0 ||
        !info) return -1;
    long long size = -1;
    char *key = NULL;
    char *value = NULL;
    while (AFCKeyValueRead(info, &key, &value) == 0 && key && value) {
        if (strcmp(key, "st_size") == 0) size = strtoll(value, NULL, 10);
        key = NULL;
        value = NULL;
    }
    AFCKeyValueClose(info);
    return size;
}

static NSString *AFCFileKind(AFCConnectionRef afc, NSString *path) {
    AFCKeyValueRef info = NULL;
    if (AFCFileInfoOpen(afc, path.fileSystemRepresentation, &info) != 0 ||
        !info) return nil;
    NSString *kind = nil;
    char *key = NULL;
    char *value = NULL;
    while (AFCKeyValueRead(info, &key, &value) == 0 && key && value) {
        if (strcmp(key, "st_ifmt") == 0)
            kind = [NSString stringWithUTF8String:value];
        key = NULL;
        value = NULL;
    }
    AFCKeyValueClose(info);
    return kind;
}

static NSData *AFCReadFile(AFCConnectionRef afc, NSString *path) {
    long long size = AFCFileSize(afc, path);
    if (size < 0 || size > 16 * 1024 * 1024) return nil;
    AFCFileRef file = NULL;
    if (AFCFileRefOpen(afc, path.fileSystemRepresentation, 1, &file) != 0 ||
        !file) return nil;
    NSMutableData *data = [NSMutableData dataWithLength:(NSUInteger)size];
    long length = (long)size;
    int status = size == 0
        ? 0 : AFCFileRefRead(afc, file, data.mutableBytes, &length);
    AFCFileRefClose(afc, file);
    if (status != 0 || length < 0 || length > size) return nil;
    data.length = (NSUInteger)length;
    return data;
}

static BOOL AFCWriteFile(AFCConnectionRef afc, NSString *path, NSData *data) {
    AFCFileRef file = NULL;
    int status = AFCFileRefOpen(afc, path.fileSystemRepresentation, 3, &file);
    if (status != 0 || !file) return NO;
    status = data.length == 0
        ? 0 : AFCFileRefWrite(afc, file, data.bytes, (long)data.length);
    int closeStatus = AFCFileRefClose(afc, file);
    return status == 0 && closeStatus == 0;
}

static BOOL EnsureDirectory(AFCConnectionRef afc, NSString *path) {
    return AFCExists(afc, path) ||
        AFCDirectoryCreate(afc, path.fileSystemRepresentation) == 0;
}

static BOOL RemoveIfPresent(AFCConnectionRef afc, NSString *path) {
    if (!AFCExists(afc, path)) return YES;
    return AFCRemovePath(afc, path.fileSystemRepresentation) == 0 &&
        !AFCExists(afc, path);
}

static BOOL AllTrackedBooksFilesAbsent(AFCConnectionRef afc) {
    for (NSUInteger index = 0;
         index < sizeof(TrackedBooksFiles) / sizeof(char *);
         index++) {
        NSString *path =
            [NSString stringWithUTF8String:TrackedBooksFiles[index]];
        if (AFCExists(afc, path)) return NO;
    }
    return YES;
}

static BOOL IsSafeRelativePath(NSString *path) {
    if (!path.length || [path hasPrefix:@"/"] || [path hasSuffix:@"/"])
        return NO;
    for (NSString *component in [path componentsSeparatedByString:@"/"])
        if (!component.length || [component isEqual:@"."] ||
            [component isEqual:@".."]) return NO;
    return YES;
}

static BOOL IsGeneratedName(NSString *value, NSString *prefix) {
    return value.length && [value hasPrefix:prefix] &&
        [value rangeOfString:@"/"].location == NSNotFound;
}

static BOOL IsCanaryLeaf(NSString *leaf) {
    return IsGeneratedName(leaf, AIRLIFT_CANARY_PREFIX) &&
        [leaf hasSuffix:@".bin"];
}

static BOOL RemoveGeneratedTree(AFCConnectionRef afc,
                                NSString *path,
                                NSUInteger depth) {
    if (depth > 32) return NO;
    NSString *kind = AFCFileKind(afc, path);
    if (!kind) return YES;
    if ([kind isEqual:@"S_IFDIR"]) {
        AFCDirectoryRef directory = NULL;
        if (AFCDirectoryOpen(afc, path.fileSystemRepresentation, &directory) !=
                0 ||
            !directory) return NO;
        NSMutableArray<NSString *> *children = NSMutableArray.array;
        BOOL readOK = YES;
        for (NSUInteger index = 0; index < 8192; index++) {
            char *raw = NULL;
            int status = AFCDirectoryRead(afc, directory, &raw);
            if (status != 0) {
                readOK = NO;
                break;
            }
            if (!raw) break;
            NSString *name = [NSString stringWithUTF8String:raw];
            if (!name || [name isEqual:@"."] || [name isEqual:@".."]) continue;
            [children addObject:name];
        }
        BOOL closeOK = AFCDirectoryClose(afc, directory) == 0;
        if (!readOK || !closeOK) return NO;
        for (NSString *name in children) {
            NSString *child = [path stringByAppendingPathComponent:name];
            if (!RemoveGeneratedTree(afc, child, depth + 1)) return NO;
        }
    }
    return AFCRemovePath(afc, path.fileSystemRepresentation) == 0 &&
        !AFCExists(afc, path);
}

static NSDictionary *SessionSummary(DeviceSession *session) {
    id productType = session->connected
        ? CFBridgingRelease(AMDeviceCopyValue(
              session->device, NULL, CFSTR("ProductType"))) : nil;
    id productVersion = session->connected
        ? CFBridgingRelease(AMDeviceCopyValue(
              session->device, NULL, CFSTR("ProductVersion"))) : nil;
    id buildVersion = session->connected
        ? CFBridgingRelease(AMDeviceCopyValue(
              session->device, NULL, CFSTR("BuildVersion"))) : nil;
    return @{
        @"subscribeStatus": @(session->subscribeStatus),
        @"targetObserved": @(session->device != NULL),
        @"connectStatus": @(session->connectStatus),
        @"validateStatus": @(session->validateStatus),
        @"sessionStatus": @(session->sessionStatus),
        @"serviceStatus": @(session->serviceStatus),
        @"afcStatus": @(session->afcStatus),
        @"productType": [productType isKindOfClass:NSString.class]
            ? productType : @"(nil)",
        @"productVersion": [productVersion isKindOfClass:NSString.class]
            ? productVersion : @"(nil)",
        @"buildVersion": [buildVersion isKindOfClass:NSString.class]
            ? buildVersion : @"(nil)",
    };
}

static BOOL TargetMatches(NSDictionary *summary,
                          NSString *product,
                          NSString *build) {
    return [summary[@"productType"] isEqual:product] &&
        [summary[@"productVersion"] isEqual:AIRLIFT_TARGET_VERSION] &&
        [summary[@"buildVersion"] isEqual:build];
}

static BOOL TargetGate(NSDictionary *summary, BOOL *tested) {
    *tested = NO;
#define AIRLIFT_MATCH_TESTED(product, build) \
    if (TargetMatches(summary, product, build)) { \
        *tested = YES; \
        return YES; \
    }
    AIRLIFT_TESTED_TARGETS(AIRLIFT_MATCH_TESTED)
#undef AIRLIFT_MATCH_TESTED

#define AIRLIFT_MATCH_EXPECTED(product, build) \
    if (TargetMatches(summary, product, build)) return YES;
    AIRLIFT_EXPECTED_TARGETS(AIRLIFT_MATCH_EXPECTED)
#undef AIRLIFT_MATCH_EXPECTED
    return NO;
}

static BOOL SendAll(AMDServiceConnectionRef service, NSData *data) {
    const uint8_t *cursor = data.bytes;
    size_t remaining = data.length;
    while (remaining) {
        int sent = AMDServiceConnectionSend(service, cursor, remaining);
        if (sent <= 0) return NO;
        cursor += sent;
        remaining -= (size_t)sent;
    }
    return YES;
}

static NSDictionary *Stage(DeviceSession *session, NSArray<NSString *> *args) {
    NSString *source = args[0];
    NSString *linkDestination = args[1];
    NSString *recovered = args[2];
    NSData *archive = [NSData dataWithContentsOfFile:args[3]];
    NSData *books = [NSData dataWithContentsOfFile:args[4]];
    BOOL safeArguments =
        IsGeneratedName(source, AIRLIFT_SOURCE_PREFIX) &&
        IsGeneratedName(linkDestination, AIRLIFT_LINK_PREFIX) &&
        IsGeneratedName(recovered, AIRLIFT_RECOVERED_PREFIX);
    BOOL booksAbsent = AllTrackedBooksFilesAbsent(session->afc);
    BOOL freshPaths = !AFCExists(session->afc, source) &&
        !AFCExists(session->afc, linkDestination) &&
        !AFCExists(session->afc, recovered);
    if (!safeArguments || !archive || !books || !booksAbsent || !freshPaths) {
        return @{ @"ok": @NO,
                  @"safeArguments": @(safeArguments),
                  @"localInputsReadable": @(archive != nil && books != nil),
                  @"booksPreimageAbsent": @(booksAbsent),
                  @"freshPaths": @(freshPaths) };
    }

    AMDServiceConnectionRef zipService = NULL;
    int serviceStatus = AMDeviceSecureStartService(
        session->device,
        CFSTR("com.apple.streaming_zip_conduit"),
        NULL,
        &zipService);
    int messageStatus = -1;
    int responseStatus = -1;
    BOOL archiveSent = NO;
    CFTypeRef response = NULL;
    if (serviceStatus == 0 && zipService) {
        messageStatus = AMDServiceConnectionSendMessage(
            zipService,
            (__bridge CFTypeRef)@{ @"MediaSubdir": source },
            kCFPropertyListBinaryFormat_v1_0);
        if (messageStatus == 0) archiveSent = SendAll(zipService, archive);
        if (archiveSent) {
            int socket = AMDServiceConnectionGetSocket(zipService);
            struct timeval timeout = { .tv_sec = 30, .tv_usec = 0 };
            setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, &timeout,
                       sizeof(timeout));
            CFPropertyListFormat format = kCFPropertyListBinaryFormat_v1_0;
            responseStatus = AMDServiceConnectionReceiveMessage(
                zipService, &response, &format);
        }
    }
    if (response) CFRelease(response);
    if (zipService) AMDServiceConnectionInvalidate(zipService);

    NSString *link =
        [source stringByAppendingPathComponent:@"p0/p1/p2/link"];
    BOOL sourceObjects = AFCExists(session->afc, source) &&
        AFCExists(session->afc, link) &&
        AFCExists(session->afc,
                  [source stringByAppendingPathComponent:@"payload"]);
    BOOL directoriesReady = EnsureDirectory(session->afc, @"Books") &&
        EnsureDirectory(session->afc, @"Books/Sync");
    BOOL booksWritten = sourceObjects && directoriesReady &&
        AFCWriteFile(session->afc, @"Books/Sync/Books.plist", books);
    BOOL ok = serviceStatus == 0 && messageStatus == 0 && archiveSent &&
        sourceObjects && booksWritten;
    return @{ @"ok": @(ok),
              @"safeArguments": @YES,
              @"booksPreimageAbsent": @YES,
              @"freshPaths": @YES,
              @"zipServiceStatus": @(serviceStatus),
              @"zipMessageStatus": @(messageStatus),
              @"zipResponseStatus": @(responseStatus),
              @"archiveSent": @(archiveSent),
              @"sourceObjectsPresent": @(sourceObjects),
              @"booksWritten": @(booksWritten) };
}

static NSDictionary *Finish(DeviceSession *session, NSArray<NSString *> *args) {
    NSString *source = args[0];
    NSString *linkDestination = args[1];
    NSString *recovered = args[2];
    NSData *expected = [NSData dataWithContentsOfFile:args[3]];
    NSString *targetTail = args[4];
    NSString *targetLeaf = args[5];
    BOOL safeArguments =
        IsGeneratedName(source, AIRLIFT_SOURCE_PREFIX) &&
        IsGeneratedName(linkDestination, AIRLIFT_LINK_PREFIX) &&
        IsGeneratedName(recovered, AIRLIFT_RECOVERED_PREFIX) &&
        IsSafeRelativePath(targetTail) && IsCanaryLeaf(targetLeaf) &&
        expected.length > 0 && expected.length < 4096;
    if (!safeArguments)
        return @{ @"ok": @NO, @"safeArguments": @NO };

    NSData *observed = AFCReadFile(session->afc, recovered);
    BOOL recoveredPresent = observed != nil;
    BOOL bytesMatch = recoveredPresent && [observed isEqualToData:expected];
    NSMutableArray<NSString *> *failures = NSMutableArray.array;

    NSString *targetThroughLink =
        [linkDestination stringByAppendingPathComponent:targetLeaf];
    if (!RemoveIfPresent(session->afc, targetThroughLink))
        [failures addObject:@"target canary"];
    BOOL targetAbsent = !AFCExists(session->afc, targetThroughLink);
    if (!RemoveIfPresent(session->afc, linkDestination))
        [failures addObject:@"relocated link"];
    if (!RemoveIfPresent(session->afc, recovered))
        [failures addObject:@"recovered file"];
    if (!RemoveGeneratedTree(session->afc, source, 0))
        [failures addObject:@"StreamingZip tree"];
    for (NSUInteger index = 0;
         index < sizeof(TrackedBooksFiles) / sizeof(char *);
         index++) {
        NSString *path =
            [NSString stringWithUTF8String:TrackedBooksFiles[index]];
        if (!RemoveIfPresent(session->afc, path))
            [failures addObject:path.lastPathComponent];
    }

    BOOL sourceAbsent = !AFCExists(session->afc, source);
    BOOL linkAbsent = !AFCExists(session->afc, linkDestination);
    BOOL recoveredAbsent = !AFCExists(session->afc, recovered);
    BOOL booksAbsent = AllTrackedBooksFilesAbsent(session->afc);
    BOOL cleanupComplete = failures.count == 0 && targetAbsent &&
        sourceAbsent && linkAbsent && recoveredAbsent && booksAbsent;
    return @{ @"ok": @(bytesMatch && cleanupComplete),
              @"safeArguments": @YES,
              @"recoveredPresent": @(recoveredPresent),
              @"recoveredBytesMatch": @(bytesMatch),
              @"observedLength": @(observed.length),
              @"cleanupComplete": @(cleanupComplete),
              @"cleanupFailureCount": @(failures.count),
              @"failures": failures,
              @"targetAbsent": @(targetAbsent),
              @"sourceAbsent": @(sourceAbsent),
              @"linkAbsent": @(linkAbsent),
              @"recoveredAbsent": @(recoveredAbsent),
              @"booksFilesAbsent": @(booksAbsent) };
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 3) return 64;
        NSString *command = [NSString stringWithUTF8String:argv[1]];
        TargetIdentifier = CFStringCreateWithCString(
            kCFAllocatorDefault, argv[2], kCFStringEncodingUTF8);
        if (!TargetIdentifier) return 64;

        DeviceSession session;
        OpenSession(&session);
        NSDictionary *summary = SessionSummary(&session);
        BOOL targetTested = NO;
        BOOL targetGatePassed = TargetGate(summary, &targetTested);
        NSDictionary *operation = nil;
        if (session.afcStatus == 0 && session.afc && targetGatePassed) {
            if ([command isEqual:@"probe"] && argc == 3) {
                operation = @{ @"ok": @YES,
                    @"booksSyncPlistPresent":
                        @(AFCExists(session.afc, @"Books/Sync/Books.plist")) };
            } else if ([command isEqual:@"stage"] && argc == 8) {
                operation = Stage(&session, @[
                    [NSString stringWithUTF8String:argv[3]],
                    [NSString stringWithUTF8String:argv[4]],
                    [NSString stringWithUTF8String:argv[5]],
                    [NSString stringWithUTF8String:argv[6]],
                    [NSString stringWithUTF8String:argv[7]],
                ]);
            } else if ([command isEqual:@"finish"] && argc == 9) {
                operation = Finish(&session, @[
                    [NSString stringWithUTF8String:argv[3]],
                    [NSString stringWithUTF8String:argv[4]],
                    [NSString stringWithUTF8String:argv[5]],
                    [NSString stringWithUTF8String:argv[6]],
                    [NSString stringWithUTF8String:argv[7]],
                    [NSString stringWithUTF8String:argv[8]],
                ]);
            }
        }

        NSMutableDictionary *result = summary.mutableCopy;
        result[@"targetGatePassed"] = @(targetGatePassed);
        result[@"targetTested"] = @(targetTested);
        result[@"command"] = command ?: @"(nil)";
        result[@"operation"] = operation ?: @{ @"ok": @NO };
        PrintJSON(result);
        BOOL ok = targetGatePassed && session.afcStatus == 0 &&
            [operation[@"ok"] boolValue];
        CloseSession(&session);
        CFRelease(TargetIdentifier);
        return ok ? 0 : 2;
    }
}
