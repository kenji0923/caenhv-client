from labscript_devices import register_classes

register_classes(
    "CAENHV",
    BLACS_tab="caenhv_client.labscript_integration.caenhv.blacs_tabs.CAENHVTab",
    runviewer_parser="",
)

