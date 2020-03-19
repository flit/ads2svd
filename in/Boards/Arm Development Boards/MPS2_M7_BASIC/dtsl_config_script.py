# Copyright (C) 2014-2019 Arm Limited (or its affiliates). All rights reserved.
from com.arm.debug.dtsl.configurations import DTSLv1
from com.arm.debug.dtsl.configurations import TimestampInfo
from com.arm.debug.dtsl.components import FormatterMode
from com.arm.debug.dtsl.components import CortexM_AHBAP
from com.arm.debug.dtsl.components import AHBCortexMMemAPAccessor
from com.arm.debug.dtsl.components import Device
from com.arm.debug.dtsl.configurations.options import IIntegerOption
from com.arm.debug.dtsl.components import DSTREAMTraceCapture
from com.arm.debug.dtsl.components import DSTREAMSTStoredTraceCapture
from com.arm.debug.dtsl.components import DSTREAMPTStoreAndForwardTraceCapture
from com.arm.debug.dtsl.components import DSTREAMPTLiveStoredStreamingTraceCapture
from com.arm.debug.dtsl.components import ETMv4TraceSource
from com.arm.debug.dtsl.components import V7M_CSTPIU
from com.arm.debug.dtsl.components import ITMTraceSource

NUM_CORES_CORTEX_M7 = 1
ATB_ID_BASE = 2
DSTREAM_PORTWIDTH = 4
ITM_ATB_ID = 1

class M_Class_ETM(ETMv4TraceSource):
    def hasTriggers(self):
        return False

    def hasTraceStartPoints(self):
        return False

    def hasTraceStopPoints(self):
        return False

    def hasTraceRanges(self):
        return False



class DtslScript(DTSLv1):
    @staticmethod
    def getOptionList():
        return [
            DTSLv1.tabSet("options", "Options", childOptions=[
                DtslScript.getOptionTraceBufferTabPage(),
                DtslScript.getOptionCortexM7TabPage(),
                DtslScript.getOptionITMTabPage()
            ])
        ]

    @staticmethod
    def getOptionTraceBufferTabPage():
        return DTSLv1.tabPage("trace", "Trace Capture", childOptions=[
                    DTSLv1.enumOption('traceCapture', 'Trace capture method', defaultValue="none",
                        values = [("none", "None"), ("DSTREAM", "DSTREAM 4GB Trace Buffer")],
                        setter=DtslScript.setTraceCaptureMethod),
                        DTSLv1.integerOption('timestampFrequency', 'Timestamp frequency', defaultValue=25000000, isDynamic=False, description="This value will be used to set the Counter Base Frequency ID Register of the Timestamp generator.\nIt represents the number of ticks per second and is used to translate the timestamp value reported into a number of seconds.\nNote that changing this value may not result in a change in the observed frequency."),
                ])

    @staticmethod
    def getOptionCortexM7TabPage():
        return DTSLv1.tabPage("coreTrace", "Core Trace", childOptions=[
                    DTSLv1.booleanOption('cortexM7coreTrace', 'Enable Cortex-M7 core trace', defaultValue=False, childOptions=[
                                                                                        DTSLv1.booleanOption('timestamp', "Enable ETM Timestamps", description="Controls the output of timestamps into the ETM output streams", defaultValue=True),
                                                                                        ETMv4TraceSource.cycleAccurateOption(DtslScript.getETMs)])
                ])

    @staticmethod
    def getOptionITMTabPage():
        return DTSLv1.tabPage("itm", "ITM", childOptions=[
                    DTSLv1.booleanOption('itm', 'Enable ITM trace', defaultValue=False,
                        setter=DtslScript.setITMEnabled),
                ])

    def __init__(self, root):
        DTSLv1.__init__(self, root)

        # locate devices on the platform and create corresponding objects
        self.discoverDevices()

        ''' Do not add directly to this list - first check if the item you are adding is already present.. '''
        self.mgdPlatformDevs = []

        # tracks which devices are managed when a trace mode is enabled
        self.mgdTraceDevs = {}

        # only MEM_AP devices are managed by default - others will be added when enabling trace, SMP etc
        if self.AHB_M not in self.mgdPlatformDevs:
            self.mgdPlatformDevs.append(self.AHB_M)

        self.exposeCores()

        self.setManagedDeviceList(self.mgdPlatformDevs)

    # +----------------------------+
    # | Target dependent functions |
    # +----------------------------+

    def discoverDevices(self):
        '''find and create devices'''

        memApDev = 0

        memApDev = self.findDevice("CSMEMAP", memApDev + 1)
        self.AHB_M = CortexM_AHBAP(self, memApDev, "CSMEMAP")

        cortexM7coreDev = 0
        self.cortexM7cores = []

        streamID = ATB_ID_BASE

        etmDev = 1
        self.ETMs  = []

        for i in range(0, NUM_CORES_CORTEX_M7):
            # create core
            cortexM7coreDev = self.findDevice("Cortex-M7", cortexM7coreDev+1)
            dev = Device(self, cortexM7coreDev, "Cortex-M7")
            self.cortexM7cores.append(dev)

            # create the ETM for this core
            etmDev = self.findDevice("CSETM", etmDev+1)
            etm = M_Class_ETM(self, etmDev, streamID, "ETM_%d_%d" % (i, streamID))
            streamID += 1
            # disabled by default - will enable with option
            etm.setEnabled(False)
            self.ETMs.append(etm)

        # TPIU
        tpiuDev = self.findDevice("CSTPIU")
        self.tpiu = self.createTPIU(tpiuDev, "TPIU")

        # ITM
        itmDev = self.findDevice("CSITM")
        self.ITM = self.createITM(itmDev, ITM_ATB_ID, "ITM")

    def createDSTREAM(self):
        self.DSTREAM = DSTREAMTraceCapture(self, "DSTREAM")


    # +----------------------------+
    # | Target dependent functions |
    # +----------------------------+
    def postConnect(self):
        DTSLv1.postConnect(self)
        freq = self.getOptionValue("options.trace.timestampFrequency")
        # update the value so the trace decoder can access it
        tsInfo = TimestampInfo(freq)
        self.setTimestampInfo(tsInfo)

    def exposeCores(self):
        for core in self.cortexM7cores:
            self.registerMClassFilters(core)
            self.addDeviceInterface(core)

    def setupDSTREAMTrace(self, portWidth):
        '''Setup DSTREAM trace capture'''

        self.setPortWidth(portWidth)

        # configure the DSTREAM for continuous trace
        self.DSTREAM.setTraceMode(DSTREAMTraceCapture.TraceMode.Continuous)

        # register other trace components
        self.DSTREAM.setTraceComponentOrder([ self.tpiu ])

        # register the DSTREAM with the configuration
        self.addTraceCaptureInterface(self.DSTREAM)

        # automatically handle connection/disconnection to trace components
        self.addManagedTraceDevices("DSTREAM", [ self.tpiu, self.DSTREAM ])

    def setPortWidth(self, portWidth):
        self.tpiu.setPortSize(portWidth)
        self.DSTREAM.setPortWidth(portWidth)

    def setTraceSourceEnabled(self, source, enabled):
        '''Enable/disable a trace source'''
        source.setEnabled(enabled)

    def createTPIU(self, tpiuDev, name):
        tpiu = V7M_CSTPIU(self, tpiuDev, name, self.cortexM7cores[0])
        # disable the TPIU by default to allow ETB to work at full rate
        tpiu.setEnabled(False)
        return tpiu

    def setDSTREAMTraceEnabled(self, enabled):
        '''Enable/disable DSTREAM trace capture'''
        self.dstreamTraceEnabled = enabled
        self.tpiu.setEnabled(enabled)

    def registerTraceSources(self, traceCapture):
        '''Register all trace sources with trace capture device'''
        for c in range(0, NUM_CORES_CORTEX_M7):
            if self.ETMs[c].isEnabled():
                self.registerCoreTraceSource(traceCapture, self.cortexM7cores[c], self.ETMs[c])

        self.registerTraceSource(traceCapture, self.ITM)

    def registerCoreTraceSource(self, traceCapture, core, source):
        '''Register a trace source with trace capture device and enable triggers'''
        # Register with trace capture, associating with core
        traceCapture.addTraceSource(source, core.getID())

        # source is managed by the configuration
        self.addManagedTraceDevices(traceCapture.getName(), [ source ])

    # +--------------------------------+
    # | Callback functions for options |
    # +--------------------------------+

    def optionValuesChanged(self):
        '''Callback to update the configuration state after options are changed'''
        if not self.isConnected():
            self.setInitialOptions()
        self.updateDynamicOptions()

    def setInitialOptions(self):
        '''Set the initial options'''
        if self.dstreamTraceEnabled:
            self.createDSTREAM()
            self.setupDSTREAMTrace(DSTREAM_PORTWIDTH)

        traceMode = self.getOptionValue("options.trace.traceCapture")

        coreTraceEnabled = self.getOptionValue("options.coreTrace.cortexM7coreTrace")
        for i in range(0, NUM_CORES_CORTEX_M7):
            self.setTraceSourceEnabled(self.ETMs[i], coreTraceEnabled)

        self.ETMs[0].setTimestampingEnabled(self.getOptionValue("options.coreTrace.cortexM7coreTrace.timestamp"))

        # register trace sources for each trace sink
        if self.dstreamTraceEnabled:
            self.registerTraceSources(self.DSTREAM)

        self.setManagedDeviceList(self.getManagedDevices(traceMode))

        dstream_opts = "options.trace.traceCapture.dstream."

        if self.dstreamTraceEnabled:
            dstream_opts = "options.trace.traceCapture." + self.getDstreamOptionString() + "."
            portWidthOpt = self.getOptions().getOption(dstream_opts + "tpiuPortWidth")
            if portWidthOpt:
                portWidth = self.getOptionValue(dstream_opts + "tpiuPortWidth")
                self.setPortWidth(int(portWidth))

            traceBufferSizeOpt = self.getOptions().getOption(dstream_opts + "traceBufferSize")
            if traceBufferSizeOpt:
                traceBufferSize = self.getOptionValue(dstream_opts + "traceBufferSize")
                self.setTraceBufferSize(traceBufferSize)

    def getDstreamOptionString(self):
        return "dstream"

    def updateDynamicOptions(self):
        '''Update the dynamic options'''

    def getManagedDevices(self, traceKey):
        '''Get the required set of managed devices for this configuration'''
        deviceList = self.mgdPlatformDevs[:]
        for d in self.mgdTraceDevs.get(traceKey, []):
            if d not in deviceList:
                deviceList.append(d)

        return deviceList

    def setTraceCaptureMethod(self, method):
        if method == "none":
            self.setDSTREAMTraceEnabled(False)
        elif method in ["DSTREAM", "DSTREAM_PT_Store_and_Forward", "DSTREAM_PT_StreamingTrace"]:
            self.setDSTREAMTraceEnabled(True)

    def getETMs(self):
        '''Get the ETMs'''
        return self.ETMs

    def setITMEnabled(self, enabled):
        '''Enable/disable the ITM trace source'''
        self.setTraceSourceEnabled(self.ITM, enabled)

    # +------------------------------+
    # | Target independent functions |
    # +------------------------------+

    def registerTraceSource(self, traceCapture, source):
        '''Register trace source with trace capture device'''
        traceCapture.addTraceSource(source)
        self.addManagedTraceDevices(traceCapture.getName(), [ source ])

    def addManagedTraceDevices(self, traceKey, devs):
        '''Add devices to the set of devices managed by the configuration for this trace mode'''
        traceDevs = self.mgdTraceDevs.get(traceKey)
        if not traceDevs:
            traceDevs = []
            self.mgdTraceDevs[traceKey] = traceDevs
        for d in devs:
            if d not in traceDevs:
                traceDevs.append(d)

    def registerMClassFilters(self, core):
        '''Register MemAP filters to allow access to the AHB for the device'''
        core.registerAddressFilters(
            [AHBCortexMMemAPAccessor("AHB", self.AHB_M, "M Class AHB bus accessed via AP")])

    def createITM(self, itmDev, streamID, name):
        itm = ITMTraceSource(self, itmDev, streamID, name)
        # disabled by default - will enable with option
        itm.setEnabled(False)
        return itm

class DtslScript_RVI(DtslScript):
    @staticmethod
    def getOptionList():
        return []

    def postConnect(self):
        DTSLv1.postConnect(self)

class DtslScript_CMSIS(DtslScript_RVI):
    pass

class DtslScript_ULINK2(DtslScript_RVI):
    pass

class DtslScript_ULINKpro(DtslScript_RVI):
    pass

class DtslScript_ULINKpro_D(DtslScript_RVI):
    pass

class DtslScript_DSTREAM_ST_Family(DtslScript):

    def setupDSTREAMTrace(self, portWidth):
        '''Setup DSTREAM trace capture'''

        self.setPortWidth(portWidth)

        # register other trace components
        self.DSTREAM.setTraceComponentOrder([ self.tpiu ])

        # register the DSTREAM with the configuration
        self.addTraceCaptureInterface(self.DSTREAM)
        self.addStreamTraceCaptureInterface(self.DSTREAM)

        # automatically handle connection/disconnection to trace components
        self.addManagedTraceDevices(self.getOptionValue("options.trace.traceCapture"), [ self.tpiu, self.DSTREAM ])

    def setTraceBufferSize(self, mode):
        '''Configuration option setter method for the trace cache buffer size'''
        cacheSize = 64*1024*1024
        if (mode == "64MB"):
            cacheSize = 64*1024*1024
        if (mode == "128MB"):
            cacheSize = 128*1024*1024
        if (mode == "256MB"):
            cacheSize = 256*1024*1024
        if (mode == "512MB"):
            cacheSize = 512*1024*1024
        if (mode == "1GB"):
            cacheSize = 1*1024*1024*1024
        if (mode == "2GB"):
            cacheSize = 2*1024*1024*1024
        if (mode == "4GB"):
            cacheSize = 4*1024*1024*1024
        if (mode == "8GB"):
            cacheSize = 8*1024*1024*1024
        if (mode == "16GB"):
            cacheSize = 16*1024*1024*1024
        if (mode == "32GB"):
            cacheSize = 32*1024*1024*1024
        if (mode == "64GB"):
            cacheSize = 64*1024*1024*1024
        if (mode == "128GB"):
            cacheSize = 128*1024*1024*1024

        self.DSTREAM.setMaxCaptureSize(cacheSize)

class DtslScript_DSTREAM_ST(DtslScript_DSTREAM_ST_Family):
    @staticmethod
    def getOptionList():
        return [
            DTSLv1.tabSet("options", "Options", childOptions=[
                DtslScript_DSTREAM_ST.getOptionTraceBufferTabPage(),
                DtslScript.getOptionCortexM7TabPage(),
                DtslScript.getOptionITMTabPage()
            ])
        ]

    @staticmethod
    def getOptionTraceBufferTabPage():
        return DTSLv1.tabPage("trace", "Trace Capture", childOptions=[
                    DTSLv1.radioEnumOption('traceCapture', 'Trace capture method', defaultValue="none",
                        values = [("none", "None"), DtslScript_DSTREAM_ST.getDSTREAMOptions()],
                        setter=DtslScript.setTraceCaptureMethod),
                        DTSLv1.integerOption('timestampFrequency', 'Timestamp frequency', defaultValue=25000000, isDynamic=False, description="This value will be used to set the Counter Base Frequency ID Register of the Timestamp generator.\nIt represents the number of ticks per second and is used to translate the timestamp value reported into a number of seconds.\nNote that changing this value may not result in a change in the observed frequency."),
                ])

    @staticmethod
    def getDSTREAMOptions():
        return (
            "DSTREAM", "Streaming Trace",
            DTSLv1.infoElement(
                "dstream", "", "",
                childOptions=[
                    DTSLv1.enumOption('tpiuPortWidth', 'TPIU port width', defaultValue="4",
                        values = [("1", "1 bit"), ("2", "2 bit"), ("4", "4 bit")],isDynamic=False),
                    DTSLv1.enumOption('traceBufferSize', 'Trace buffer size', defaultValue="4GB",
                        values = [("64MB", "64MB"), ("128MB", "128MB"), ("256MB", "256MB"), ("512MB", "512MB"),
                                  ("1GB", "1GB"), ("2GB", "2GB"), ("4GB", "4GB"), ("8GB", "8GB"), ("16GB", "16GB"),
                                  ("32GB", "32GB"), ("64GB", "64GB"), ("128GB", "128GB")], isDynamic=False)
                ]
            )
        )

    def createDSTREAM(self):
        self.DSTREAM = DSTREAMSTStoredTraceCapture(self, "DSTREAM")

class DtslScript_DSTREAM_PT(DtslScript_DSTREAM_ST_Family):
    @staticmethod
    def getOptionList():
        return [
            DTSLv1.tabSet("options", "Options", childOptions=[
                DtslScript_DSTREAM_PT.getOptionTraceBufferTabPage(),
                DtslScript.getOptionCortexM7TabPage(),
                DtslScript.getOptionITMTabPage()
            ])
        ]

    @staticmethod
    def getOptionTraceBufferTabPage():
        return DTSLv1.tabPage("trace", "Trace Capture", childOptions=[
                    DTSLv1.radioEnumOption('traceCapture', 'Trace capture method', defaultValue="none",
                        values = [("none", "None"), DtslScript_DSTREAM_PT.getStoreAndForwardOptions(), DtslScript_DSTREAM_PT.getStreamingTraceOptions()],
                        setter=DtslScript_DSTREAM_PT.setTraceCaptureMethod),
                        DTSLv1.integerOption('timestampFrequency', 'Timestamp frequency', defaultValue=25000000, isDynamic=False, description="This value will be used to set the Counter Base Frequency ID Register of the Timestamp generator.\nIt represents the number of ticks per second and is used to translate the timestamp value reported into a number of seconds.\nNote that changing this value may not result in a change in the observed frequency."),
                ])

    @staticmethod
    def getStoreAndForwardOptions():
        return (
            "DSTREAM_PT_Store_and_Forward", "DSTREAM-PT 8GB Trace Buffer",
            DTSLv1.infoElement(
                "dpt_storeandforward", "", "",
                childOptions=[
                    DTSLv1.enumOption('tpiuPortWidth', 'TPIU port width', defaultValue="4",
                        values = [("1", "1 bit"), ("2", "2 bit"), ("3", "3 bit"), ("4", "4 bit")], isDynamic=False)
                ]
            )
        )

    @staticmethod
    def getStreamingTraceOptions():
        return (
            "DSTREAM_PT_StreamingTrace", "DSTREAM-PT Streaming Trace",
            DTSLv1.infoElement(
                "dpt_streamingtrace", "", "",
                childOptions=[
                    DTSLv1.enumOption('tpiuPortWidth', 'TPIU port width', defaultValue="4",
                        values = [("1", "1 bit"), ("2", "2 bit"), ("3", "3 bit"), ("4", "4 bit")],isDynamic=False),
                    DTSLv1.enumOption('traceBufferSize', 'Host trace buffer size', defaultValue="4GB",
                        values = [("64MB", "64MB"), ("128MB", "128MB"), ("256MB", "256MB"), ("512MB", "512MB"),
                                  ("1GB", "1GB"), ("2GB", "2GB"), ("4GB", "4GB"), ("8GB", "8GB"), ("16GB", "16GB"),
                                  ("32GB", "32GB"), ("64GB", "64GB"), ("128GB", "128GB")], isDynamic=False)
                ]
            )
        )

    def getDstreamOptionString(self):
        if self.getOptionValue("options.trace.traceCapture") == "DSTREAM_PT_Store_and_Forward":
            return "dpt_storeandforward"
        if self.getOptionValue("options.trace.traceCapture") == "DSTREAM_PT_StreamingTrace":
            return "dpt_streamingtrace"

    def createDSTREAM(self):
        if self.getOptionValue("options.trace.traceCapture") == "DSTREAM_PT_Store_and_Forward":
            self.DSTREAM = DSTREAMPTStoreAndForwardTraceCapture(self, "DSTREAM_PT_Store_and_Forward")
        elif self.getOptionValue("options.trace.traceCapture") == "DSTREAM_PT_StreamingTrace":
            self.DSTREAM = DSTREAMPTLiveStoredStreamingTraceCapture(self, "DSTREAM_PT_StreamingTrace")