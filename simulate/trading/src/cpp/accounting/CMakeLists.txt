file(GLOB accounting_sources CONFIGURE_DEPENDS "*.cpp")
add_library(accounting STATIC ${accounting_sources})
add_library(taosim::accounting ALIAS accounting)

target_include_directories(accounting PUBLIC include)

target_link_libraries(accounting
    PUBLIC
        taosim::decimal
        taosim::exchange
        taosim::mp
        taosim::serialization
        taosim::util
        Boost::boost
)
