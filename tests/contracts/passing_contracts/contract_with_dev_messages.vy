# @version 0.3.4

# Test dev messages in various code placements
@external
def foo() -> bool:
    return True # dev: foo


# dev: bar
@external
def bar() -> bool:
    return True


@external
def baz() -> bool: # dev: baz
    return True

# Test that whitespacing is non-specific
#          dev    :      odd spacing

# Test non-ascii character matching
# dev: 你好，猿

# Test that empty dev comments are ignored
# dev:
