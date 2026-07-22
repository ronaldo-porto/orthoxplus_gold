add_library(decimal INTERFACE)
add_library(taosim::decimal ALIAS decimal)

target_include_directories(decimal INTERFACE include)

target_link_libraries(decimal INTERFACE bdl)