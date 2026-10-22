set(DOOM "${CMAKE_CURRENT_LIST_DIR}/../third_party/doomgeneric/doomgeneric")
set(NAMES dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop d_main d_mode d_net
    f_finale f_wipe g_game hu_lib hu_stuff info i_cdmus i_endoom i_joystick i_scale i_timer memio
    m_argv m_bbox m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors
    p_enemy p_floor p_inter p_lights p_map p_maputl p_mobj p_plats p_pspr p_saveg p_setup p_sight
    p_spec p_switch p_telept p_tick p_user r_bsp r_data r_draw r_main r_plane r_segs r_sky r_things
    sha1 sounds statdump st_lib st_stuff s_sound tables v_video wi_stuff w_checksum w_file w_main w_wad z_zone)
set(SOURCES app.c compat.c system.c wad.c video.c sound.c gpu.c loader.c loader_dl.c)
foreach(name ${NAMES})
    list(APPEND SOURCES "${DOOM}/${name}.c")
endforeach()
idf_component_register(SRCS ${SOURCES} INCLUDE_DIRS "../include" "${DOOM}" "../firmware/bl616/copro")
foreach(name ${NAMES})
    set_source_files_properties("${DOOM}/${name}.c" PROPERTIES COMPILE_OPTIONS
        "-include;${CMAKE_CURRENT_LIST_DIR}/../include/compat.h;-Wno-error;-Wno-incompatible-pointer-types;-Wno-int-conversion")
endforeach()
target_compile_definitions(${COMPONENT_LIB} PRIVATE VDAC4 NORMALUNIX LINUX DOOMGENERIC_RESX=320 DOOMGENERIC_RESY=200)
target_compile_options(${COMPONENT_LIB} PRIVATE -std=gnu11 -O2 -fno-strict-aliasing -fwrapv -fstack-usage -Wno-error=misleading-indentation)
