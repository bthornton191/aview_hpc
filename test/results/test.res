<?xml version="1.0" encoding="UTF-8"?>
<Results xmlns="http://www.mscsoftware.com/:xrf10">
<Bibliography>
<File URI="file:///home/ben.thornton/.tmp/tmp.0IPj3YbXtD/test.res" schema="xrf" version="2.0.0.0" publicationDate="2023-06-13 22:52:01+00:00" />
<Corporation author="MSC.Software" URI="http://www.mscsoftware.com/" />
<Author user="ben.thornton@am.hexagonmetrology.com" name="THORNTON Ben" />
<Revision version="1" derivedFrom="-unknown-">
<Comment>

</Comment>
</Revision>
</Bibliography>
<Analysis name="ANALYSIS_01" executionDate="2023-06-13 22:52:01" Solver="Adams 2022.1                 Adams Solver (C++)"  script="-unknown-">
<Bibliography>
<Environment operatingSystem="Linux 4.18.0-477.13.1.el8_8.x86_64" hostname="DEWET-EMEA-MCS1" />
<Application name="Adams View" version="2022.01.00" />
</Bibliography>
<ModelInfo title="Adams View model name: MODEL_1" creationDate="-unknown-" checksum="-unknown-" />
<Units angle="rad" length="inch" mass="pound_mass" time="sec" />
<StepMap name="map_001">
<Entity name="time">
<Component name="TIME" unitsValue="sec" id="1" />
</Entity>
<Entity name="PART_2_XFORM" entity="PART_2" entType="Part" objectId="2">
<Component name="X" unitsValue="inch" id="2" />
<Component name="Y" unitsValue="inch" id="3" />
<Component name="Z" unitsValue="inch" id="4" />
<Component name="PSI" unitsValue="rad" id="5" />
<Component name="THETA" unitsValue="rad" id="6" />
<Component name="PHI" unitsValue="rad" id="7" />
<Component name="VX" unitsValue="inch/sec" id="8" />
<Component name="VY" unitsValue="inch/sec" id="9" />
<Component name="VZ" unitsValue="inch/sec" id="10" />
<Component name="WX" unitsValue="rad/sec" id="11" />
<Component name="WY" unitsValue="rad/sec" id="12" />
<Component name="WZ" unitsValue="rad/sec" id="13" />
<Component name="ACCX" unitsValue="inch/sec**2" id="14" />
<Component name="ACCY" unitsValue="inch/sec**2" id="15" />
<Component name="ACCZ" unitsValue="inch/sec**2" id="16" />
<Component name="WDX" unitsValue="rad/sec**2" id="17" />
<Component name="WDY" unitsValue="rad/sec**2" id="18" />
<Component name="WDZ" unitsValue="rad/sec**2" id="19" />
</Entity>
</StepMap>
<Data name="modelInput_001" id="1">
<Step type="input">
0
0 0 0 0 0 0
0 0 0 0 0 0
0 0 0 0 0 0
</Step>
</Data>
<Data name="initialConditions_001" id="2">
<Step type="initialConditions">
0
0 0 0 0 0 0
0 0 0 0 0 0
0 -386.08858270000001767 0 0 0 0
</Step>
</Data>
<Data name="dynamic_001" id="3">
<Step type="dynamic">
0.01
0 -0.0194492123535125 0 0 0 0
0 -3.8608858270000006 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.02
0 -0.07736249975851248 0 0 0 0
0 -7.72177165400000032 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.03
0 -0.17388464543351248 0 0 0 0
0 -11.58265748100000181 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.04
0 -0.30901564937851245 0 0 0 0
0 -15.44354330800000241 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.05
0 -0.48275551159351243 0 0 0 0
0 -19.30442913500000301 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.06
0 -0.69510423207851235 0 0 0 0
0 -23.16531496200000362 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.07000000000000001
0 -0.94606181083351237 0 0 0 0
0 -27.02620078900000067 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.08
0 -1.23562824785851233 0 0 0 0
0 -30.88708661600000482 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.09
0 -1.56380354315351244 0 0 0 0
0 -34.74797244300000898 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.09999999999999999
0 -1.93058769671851249 0 0 0 0
0 -38.60885827000001314 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.10999999999999999
0 -2.33598070855351247 0 0 0 0
0 -42.46974409700001729 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.11999999999999998
0 -2.77998257865851262 0 0 0 0
0 -46.33062992400002145 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.12999999999999998
0 -3.26259330703351269 0 0 0 0
0 -50.1915157510000256 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.13999999999999999
0 -3.78381289367851226 0 0 0 0
0 -54.05240157800002976 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.14999999999999999
0 -4.34364133859351309 0 0 0 0
0 -57.91328740500003391 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.16
0 -4.94207864177851341 0 0 0 0
0 -61.77417323200003807 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.17000000000000001
0 -5.57912480323351367 0 0 0 0
0 -65.63505905900004223 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.18000000000000002
0 -6.25477982295851387 0 0 0 0
0 -69.49594488600004638 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.19000000000000003
0 -6.969043700953514 0 0 0 0
0 -73.35683071300005054 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.20000000000000004
0 -7.72191643721851406 0 0 0 0
0 -77.21771654000005469 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.21000000000000005
0 -8.51339803175351406 0 0 0 0
0 -81.07860236700005885 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.22000000000000006
0 -9.34348848455851311 0 0 0 0
0 -84.939488194000063 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.23000000000000007
0 -10.21218779563351298 0 0 0 0
0 -88.80037402100006716 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.24000000000000007
0 -11.11949596497851189 0 0 0 0
0 -92.6612598480000571 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
<Step type="dynamic">
0.25
0 -12.06541299259351163 0 0 0 0
0 -96.52214567500006126 0 0 0 0
0 -386.08858270000007451 0 0 0 0
</Step>
</Data>
<TerminationStatus runTermObject="" runStatus="Successful" />
</Analysis>
</Results>
