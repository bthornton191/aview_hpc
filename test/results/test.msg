
-------------------------------------------------------------------------------
|                                                                             |
|                  ,                                                          |
|               ***,                    Hexagon                               |
|          ((((((,,,                    -------                               |
|      (((((((((,,,,            *                                             |
|      *((((((,,,,,,          **(                                             |
|      **((((,,,,,,,     ******((       A d a m s   C++   S o l v e r         |
|      ****,,,,,,,,, ********((((       Build:   2022.1.0-CL875404            |
|      ***  ,,,,,(((********(((((       Version: Adams 2022.1                 |
|      *.   **((((((******(((((((                                             |
|      ********(((((*****((((((((                                             |
|               ,,,,   .,,,,,,,                                               |
|                 ,,  ,,,,              http://www.mscsoftware.com            |
|                  , ,                                                        |
|                   ,                                                         |
|                                                                             |
|    *********************************************************************    |
|    *                                                                   *    |
|    *              Hexagon Manufacturing Intelligence, Inc.             *    |
|    *                                                                   *    |
|    *                             A d a m s                             *    |
|    *                                                                   *    |
|    *          Automatic Dynamic Analysis of Mechanical Systems         *    |
|    *                                                                   *    |
|    *********************************************************************    |
|    *                                                                   *    |
|    *                         LEGAL INFORMATION                         *    |
|    *                                                                   *    |
|    *  Warning:  This computer  program is protected  by copyright law  *    |
|    *  and  international  treaties.    Unauthorized  reproduction  or  *    |
|    *  distribution  of this  computer program, or any  portion of it,  *    |
|    *  may result in severe civil and criminal penalties.               *    |
|    *                                                                   *    |
|    *  Copyright (C) 2022 Hexagon AB and/or its subsidiaries.           *    |
|    *                        All rights reserved.                       *    |
|    *                                                                   *    |
|    *********************************************************************    |
|                                                                             |
|-----------------------------------------------------------------------------|
|                                                                             |
|    *********************************************************************    |
|    *                                                                   *    |
|    *   Local Time: 2023-06-13 22:52:01                                 *    |
|    *    Host Name: DEWET-EMEA-MCS1                                     *    |
|    *        Uname: Linux                                               *    |
|    *      OS Info: 4.18.0-477.13.1.el8_8.x86_64                        *    |
|    *         User: ben.thornton@am.hexagonmetrology.com                *    |
|    *  Customer ID: E5EE72D7-936A8441                                   *    |
|    *  Current dir: /home/ben.thornton/.tmp/tmp.0IPj3YbXtD              *    |
|    *   Process ID: 135416                                              *    |
|    *                                                                   *    |
|    *********************************************************************    |
|                                                                             |
-------------------------------------------------------------------------------

 command: FILE/COMMAND=test.acf
 command: 
 command: file/model=test

-------------------------------------------------------------------------------
|                                 Model Title                                 |
|=============================================================================|
|                                                                             |
|                       Adams View model name: MODEL_1                        |
|                                                                             |
-------------------------------------------------------------------------------

 command: ! Insert ACF commands here:
 command: SIMULATE/DYNAMIC, END=0.25, DTOUT=1.0E-02


  Begin Simulation

  ****** Performing Dynamic Simulation using Gstiff I3 Integrator ******
   The system is modelled with INDEX-3 DAEs.
   The integrator is GSTIFF, CORRECTOR = original
   Integration error =    1.000000E-03

   Simulation   Step          Function     Cumulative    Integration   Elapsed 
   Time         Size          Evaluations  Steps Taken   Order         Time    
   ___________  ___________   ___________  ___________   ___________   ________
   0.00000E+00  5.00000E-04            0            0             1       0.04 
   2.00000E-02  1.00000E-02           11            8             2       0.04 
   4.00000E-02  1.00000E-02           13           10             2       0.04 
   6.00000E-02  1.00000E-02           15           12             2       0.04 
   8.00000E-02  1.00000E-02           17           14             2       0.04 
   1.00000E-01  1.00000E-02           19           16             2       0.04 
   1.20000E-01  1.00000E-02           21           18             2       0.04 
   1.40000E-01  1.00000E-02           23           20             2       0.04 
   1.60000E-01  1.00000E-02           25           22             2       0.04 
   1.80000E-01  1.00000E-02           27           24             2       0.04 
   2.00000E-01  1.00000E-02           29           26             2       0.04 
   2.20000E-01  1.00000E-02           31           28             2       0.04 
   2.40000E-01  1.00000E-02           33           30             2       0.04 
   2.50000E-01  1.00000E-02           34           31             2       0.04 

  End Simulation
 command: stop


Finished -----
Elapsed time = 0.05s,  CPU time = 0.01s,  Average CPU load = 21.77%

