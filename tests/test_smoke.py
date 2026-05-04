def test_import_package():
    import quantumbpm

    assert quantumbpm.__version__


def test_public_api_exports():
    from quantumbpm import (
        BpmnClient,
        BpmnError,
        DmnClient,
        DmnResult,
        Handler,
        Job,
        QuantumBPM,
        StaticTokenProvider,
        TokenProvider,
        Vars,
        Worker,
        ZitadelTokenProvider,
    )

    assert all(
        cls is not None
        for cls in (
            BpmnClient,
            BpmnError,
            DmnClient,
            DmnResult,
            Handler,
            Job,
            QuantumBPM,
            StaticTokenProvider,
            TokenProvider,
            Vars,
            Worker,
            ZitadelTokenProvider,
        )
    )
