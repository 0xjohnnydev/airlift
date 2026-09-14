#ifndef AIRLIFT_TARGET_H
#define AIRLIFT_TARGET_H

#define AIRLIFT_TARGET_VERSION @"27.0"

#define AIRLIFT_TESTED_TARGETS(X) \
    X(@"iPhone18,2", @"24A435")  \
    X(@"iPhone17,1", @"24A5390f")

#define AIRLIFT_EXPECTED_TARGETS(X) \
    X(@"iPhone18,2", @"24A437")

#define AIRLIFT_SOURCE_PREFIX @"airlift-src-"
#define AIRLIFT_LINK_PREFIX @"airlift-link-"
#define AIRLIFT_RECOVERED_PREFIX @"airlift-recovered-"
#define AIRLIFT_CANARY_PREFIX @"airlift-canary-"

#endif
