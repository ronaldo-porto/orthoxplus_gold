file(GLOB sources CONFIGURE_DEPENDS "*.cpp")
add_library(event STATIC ${sources})
add_library(taosim::event ALIAS event)

target_include_directories(event PUBLIC include)

target_link_libraries(event
    PRIVATE
        taosim::decimal
        taosim::util
)